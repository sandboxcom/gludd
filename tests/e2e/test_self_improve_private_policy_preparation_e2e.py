"""Hermetic E2E proof for private-policy preparation composition."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.self_improve.runtime as runtime_module
from general_ludd.local_model import get_model
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    CodexReference,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    GeneratedProposal,
    ManagedOutcomeAdapter,
    ManagedRunResult,
    PlanBoundProposal,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.runtime import (
    MakeResult,
    build_managed_self_improve_runner,
    prepare_managed_self_improve_plan,
)

pytestmark = pytest.mark.e2e

_BASELINE_SHA = "a" * 40
_REFERENCE_SHA = "b" * 40
_PUBLIC_PATH = "src/public/value.py"
_PRIVATE_PATH = "src/private/pricing.py"
_TEST_PATH = "tests/unit/test_public_value.py"
_PRIVATE_CANARY_BYTES = b"GLUDD_PREPARATION_PRIVATE_CANARY_6c0d"
_PRIVATE_CANARY = _PRIVATE_CANARY_BYTES.decode("ascii")


def _write_policy(
    root: Path,
    *,
    default_access: str = "public",
    private_paths: tuple[str, ...] = (),
    public_paths: tuple[str, ...] = (),
) -> Path:
    policy_path = root / ".gludd" / "self-improve-policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_access": default_access,
                "private_paths": list(private_paths),
                "public_paths": list(public_paths),
            }
        ),
        encoding="utf-8",
    )
    return policy_path


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.145",
        objective="Update the approved public value without observing private code.",
        canonical_make_commands=(
            f"make test-specific TESTFILE={_TEST_PATH}",
        ),
    )


class _HermeticMakeRunner:
    """Materialize one bounded baseline behind the production Make protocol."""

    def __init__(
        self,
        context_root: Path,
        *,
        changed_paths: tuple[str, ...] = (_PUBLIC_PATH,),
        source_bytes: bytes = b"VALUE = 0\n",
    ) -> None:
        self.context_root = context_root
        self.changed_paths = changed_paths
        self.source_bytes = source_bytes
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _result(self, target: str, stdout: str = "") -> MakeResult:
        return MakeResult(("make", target), 0, stdout, "", 0.001)

    def run(
        self,
        target: str,
        variables: dict[str, str] | None = None,
        *,
        timeout: int = 120,
        read_only: bool = False,
    ) -> MakeResult:
        del timeout, read_only
        self.calls.append((target, dict(variables or {})))
        if target == "git-show-name-only":
            return self._result(target, "\n".join(self.changed_paths) + "\n")
        if target == "git-show-full":
            return self._result(target, "-VALUE = 0\n+VALUE = 1\n")
        if target == "agent-worktree-base":
            self.context_root.mkdir(parents=True, exist_ok=True)
            materialized = {
                _PUBLIC_PATH: self.source_bytes,
                _PRIVATE_PATH: _PRIVATE_CANARY_BYTES,
                _TEST_PATH: b"def test_public_value() -> None:\n    assert True\n",
            }
            for relative, content in materialized.items():
                destination = self.context_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            return self._result(target, f"WORKTREE_PATH={self.context_root}\n")
        if target == "agent-cleanup":
            return self._result(target)
        raise AssertionError(f"unexpected Make target: {target}")

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        del command, timeout
        raise AssertionError("unexpected mechanical command")

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: float,
    ) -> MakeResult:
        del target, variables, timeout
        raise AssertionError("provider must use the injected hermetic boundary")


class _Reservation:
    def mark_eligible(self, _identity: object) -> None:
        return None

    def mark_failed(self, _identity: object) -> None:
        return None


class _ModelManager:
    def __init__(self, cache_root: Path) -> None:
        self.cache_root = cache_root

    def resolve_revision(self, _repo_id: str) -> str:
        return "c" * 40

    def owned_identities_for_model_ids(
        self,
        _model_ids: tuple[str, ...],
    ) -> tuple[object, ...]:
        return ()

    @contextmanager
    def reserve_plan(self, *_args: object, **_kwargs: object) -> Iterator[_Reservation]:
        yield _Reservation()

    @contextmanager
    def acquire(self, *_args: object, **_kwargs: object) -> Iterator[Any]:
        yield SimpleNamespace(
            path=self.cache_root / "hermetic-model.gguf",
            model_id="qwen2.5-coder-0.5b",
            source="hermetic",
            resolved_revision="c" * 40,
            artifact_sha256="d" * 64,
            lease_path=self.cache_root / "hermetic-model.lease",
        )


class _Outcomes:
    planner_store = object()

    def __init__(self) -> None:
        self.memory: list[dict[str, object]] = []
        self.training: list[dict[str, object]] = []

    def load_failed_model_ids(self, **kwargs: object) -> tuple[str, ...]:
        self.memory.append(kwargs)
        return ()

    def record_outcome(self, **kwargs: object) -> str:
        self.training.append(kwargs)
        return f"record-{len(self.training)}"


@dataclass
class _Trace:
    events: list[str] = field(default_factory=list)
    prompts: list[object] = field(default_factory=list)
    calibration: list[dict[str, object]] = field(default_factory=list)
    evaluator_calls: list[dict[str, object]] = field(default_factory=list)
    results: list[ManagedRunResult] = field(default_factory=list)


def _candidate() -> PlannedModelCandidate:
    model = get_model("qwen2.5-coder-0.5b")
    assert model is not None
    return PlannedModelCandidate(model, "c" * 40, 0.5, 0)


def _proposal(reference_sha: str = _BASELINE_SHA) -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": reference_sha,
                "task_id": "S83.145",
                "edits": [
                    {
                        "operation": "replace",
                        "path": _PUBLIC_PATH,
                        "old_text": "VALUE = 0\n",
                        "new_text": "VALUE = 1\n",
                    }
                ],
                "tests": [_TEST_PATH],
                "make_commands": [
                    f"make test-specific TESTFILE={_TEST_PATH}",
                ],
                "commit_message": "feat: update public value",
            }
        )
    )


def _accepted(bound: PlanBoundProposal) -> AttemptResult:
    return AttemptResult(
        comparison=ComparisonResult(
            score=1.0,
            accepted=True,
            blockers=(),
            changed_file_precision=1.0,
            changed_file_recall=1.0,
        ),
        evidence=CandidateEvidence(
            changed_files=frozenset({_PUBLIC_PATH}),
            tests_passed=True,
            warnings=0,
            coverage_aggregate=100.0,
            coverage_min_file=100.0,
            ruff_passed=True,
            mypy_passed=True,
            docstrings_passed=True,
            markdown_passed=True,
            cleanup_passed=True,
            commit_count=1,
            worktree_clean=True,
            elapsed_seconds=0.01,
            changed_lines=1,
        ),
        patch_equivalence="equivalent",
        proposal=bound.proposal,
        diagnostics="",
        attempt_identity_digest=bound.attempt_identity_digest,
    )


def _prepare(
    root: Path,
    runner: _HermeticMakeRunner,
    events: list[str],
    *,
    project_id: str,
) -> ApprovedSelfImprovePlan:
    return prepare_managed_self_improve_plan(
        root,
        approval_id=f"approval-{project_id}",
        todo_id="todo-private-policy-preparation-e2e",
        project_id=project_id,
        baseline_ref=_BASELINE_SHA,
        reference_ref=_REFERENCE_SHA,
        task=_task(),
        max_attempts=1,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
        progress_sink=events.append,
    )


def _compose_and_run(
    root: Path,
    plan: ApprovedSelfImprovePlan,
    runner: _HermeticMakeRunner,
    trace: _Trace,
    outcomes: _Outcomes,
    monkeypatch: pytest.MonkeyPatch,
) -> ManagedRunResult:
    cache_root = root / ".model-cache"
    cache_root.mkdir(exist_ok=True)
    monkeypatch.setattr(
        runtime_module,
        "ModelLeaseManager",
        lambda **_kwargs: _ModelManager(cache_root),
    )

    def plan_candidates(*args: object, **kwargs: object) -> tuple[PlannedModelCandidate, ...]:
        trace.calibration.append(
            {
                "objective": args[0],
                "required_output_tokens": args[1],
                "prior_failures": args[2],
                "input_tokens": kwargs["input_tokens"],
            }
        )
        return (_candidate(),)

    def generate(
        _runner: object,
        _model: Path,
        prompt: object,
        _task_spec: TaskSpec,
        reference: CodexReference,
    ) -> GeneratedProposal:
        trace.prompts.append(prompt)
        return GeneratedProposal(_proposal(reference.baseline_sha))

    def evaluate(
        _root_runner: object,
        _task_spec: TaskSpec,
        _reference: CodexReference,
        bound: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        trace.evaluator_calls.append(
            {
                "attempt": attempt,
                "attempt_identity_digest": expected_attempt_identity_digest,
                "merge": merge,
            }
        )
        assert bound.attempt_identity_digest == expected_attempt_identity_digest
        assert merge is False
        return _accepted(bound)

    monkeypatch.setattr(runtime_module, "plan_model_candidates", plan_candidates)
    monkeypatch.setattr(runtime_module, "unified_probe", lambda: object())
    monkeypatch.setattr(runtime_module, "_generate_local_proposal_plan_result", generate)
    service = build_managed_self_improve_runner(
        root,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
        attempt_evaluator=cast(Any, evaluate),
        outcome_adapter_factory=lambda _cache: cast(ManagedOutcomeAdapter, outcomes),
        progress_sink=trace.events.append,
    )
    result = service.run(plan)
    trace.results.append(result)
    return result


def _observable(
    *,
    trace: _Trace,
    outcomes: _Outcomes,
    runner: _HermeticMakeRunner,
    stdout: str,
    errors: tuple[BaseException, ...] = (),
) -> str:
    return json.dumps(
        {
            "calibration": trace.calibration,
            "errors": [str(error) for error in errors],
            "events": trace.events,
            "logs": runner.calls,
            "memory": outcomes.memory,
            "prompts": [repr(prompt) for prompt in trace.prompts],
            "results": [repr(result) for result in trace.results],
            "stdout": stdout,
            "training": outcomes.training,
        },
        default=str,
        sort_keys=True,
    )


def test_prepared_allowlisted_public_work_reaches_provider_evaluator_and_learning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(
        root,
        default_access="private",
        public_paths=(_PUBLIC_PATH, _TEST_PATH),
    )
    private_file = root / _PRIVATE_PATH
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(_PRIVATE_CANARY_BYTES)
    runner = _HermeticMakeRunner(tmp_path / "baseline")
    trace = _Trace()
    outcomes = _Outcomes()

    plan = _prepare(root, runner, trace.events, project_id="public-composition")
    result = _compose_and_run(root, plan, runner, trace, outcomes, monkeypatch)
    captured = capsys.readouterr()

    assert result.accepted is True
    assert len(trace.prompts) == 1
    assert len(trace.calibration) == 1
    assert len(trace.evaluator_calls) == 1
    assert len(outcomes.memory) == 1
    assert len(outcomes.training) == 1
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
    )
    assert _PRIVATE_CANARY not in observed
    assert _PRIVATE_PATH not in observed


def test_malformed_policy_stops_preparation_before_repository_or_provider_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    policy = _write_policy(root)
    policy.write_bytes(b"{" + _PRIVATE_CANARY_BYTES)
    runner = _HermeticMakeRunner(tmp_path / "baseline")
    trace = _Trace()
    outcomes = _Outcomes()
    provider_calls: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda *args: provider_calls.append(args),
    )

    with pytest.raises(SelfImprovePolicyViolation) as captured_error:
        _prepare(root, runner, trace.events, project_id="malformed-composition")
    captured = capsys.readouterr()

    assert runner.calls == []
    assert provider_calls == []
    assert outcomes.memory == []
    assert outcomes.training == []
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed


def test_policy_drift_between_preparation_and_composed_run_blocks_all_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=(_PRIVATE_PATH,))
    private_file = root / _PRIVATE_PATH
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(_PRIVATE_CANARY_BYTES)
    runner = _HermeticMakeRunner(tmp_path / "baseline")
    trace = _Trace()
    outcomes = _Outcomes()
    plan = _prepare(root, runner, trace.events, project_id="drift-composition")
    _write_policy(root, private_paths=(_PUBLIC_PATH,))

    with pytest.raises(SelfImprovePolicyViolation) as captured_error:
        _compose_and_run(root, plan, runner, trace, outcomes, monkeypatch)
    captured = capsys.readouterr()

    assert trace.prompts == []
    assert trace.calibration == []
    assert trace.evaluator_calls == []
    assert trace.results == []
    assert outcomes.memory == []
    assert outcomes.training == []
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed


def test_composed_runs_keep_opposite_project_policies_isolated_for_same_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    public_root = tmp_path / "public-project"
    private_root = tmp_path / "private-project"
    public_root.mkdir()
    private_root.mkdir()
    _write_policy(public_root, private_paths=(_PRIVATE_PATH,))
    _write_policy(private_root, private_paths=(_PUBLIC_PATH,))
    private_source = private_root / _PUBLIC_PATH
    private_source.parent.mkdir(parents=True)
    private_source.write_bytes(_PRIVATE_CANARY_BYTES)
    trace = _Trace()
    outcomes = _Outcomes()
    public_runner = _HermeticMakeRunner(tmp_path / "public-baseline")

    public_plan = _prepare(
        public_root,
        public_runner,
        trace.events,
        project_id="same-path-public-composition",
    )
    public_result = _compose_and_run(
        public_root,
        public_plan,
        public_runner,
        trace,
        outcomes,
        monkeypatch,
    )
    provider_count = len(trace.prompts)
    private_runner = _HermeticMakeRunner(
        tmp_path / "private-baseline",
        source_bytes=_PRIVATE_CANARY_BYTES,
    )

    with pytest.raises(SelfImprovePolicyViolation) as captured_error:
        _prepare(
            private_root,
            private_runner,
            trace.events,
            project_id="same-path-private-composition",
        )
    captured = capsys.readouterr()

    assert public_result.accepted is True
    assert provider_count == 1
    assert len(trace.prompts) == provider_count
    assert len(trace.evaluator_calls) == 1
    assert len(outcomes.memory) == 1
    assert len(outcomes.training) == 1
    assert [target for target, _variables in private_runner.calls] == [
        "git-show-name-only"
    ]
    assert not (tmp_path / "private-baseline").exists()
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=private_runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed
