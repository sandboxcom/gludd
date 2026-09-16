"""Hermetic end-to-end proof for project-private self-improvement policy."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
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
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import (
    ApprovedSelfImprovePlan,
    AttemptResult,
    GeneratedProposal,
    ManagedOutcomeAdapter,
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

_BASELINE_SHA = "a" * 40
_REFERENCE_SHA = "b" * 40
_PUBLIC_PATH = "src/public/value.py"
_PRIVATE_PATH = "src/private/pricing.py"
_TEST_PATH = "tests/unit/test_public_value.py"
_PRIVATE_CANARY_BYTES = b"GLUDD_PRIVATE_CANARY_BYTES_23dfe71a"
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
    """Materialize one bounded fake baseline behind the production Make protocol."""

    def __init__(
        self,
        context_root: Path,
        *,
        changed_paths: tuple[str, ...] = (_PUBLIC_PATH,),
        source_bytes: bytes = b"VALUE = 0\n",
        after_name_lookup: Callable[[], object] | None = None,
    ) -> None:
        self.context_root = context_root
        self.changed_paths = changed_paths
        self.source_bytes = source_bytes
        self.after_name_lookup = after_name_lookup
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
            if self.after_name_lookup is not None:
                self.after_name_lookup()
            return self._result(target, "\n".join(self.changed_paths) + "\n")
        if target == "git-show-full":
            return self._result(
                target,
                f"-{_PRIVATE_CANARY}\n+public replacement\n",
            )
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
        timeout: int,
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
    results: list[object] = field(default_factory=list)


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
        todo_id="todo-private-policy-e2e",
        project_id=project_id,
        baseline_ref=_BASELINE_SHA,
        reference_ref=_REFERENCE_SHA,
        task=_task(),
        max_attempts=1,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
        progress_sink=events.append,
    )


def _run_public_plan(
    root: Path,
    plan: ApprovedSelfImprovePlan,
    runner: _HermeticMakeRunner,
    trace: _Trace,
    outcomes: _Outcomes,
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    cache_root = root / ".model-cache"
    cache_root.mkdir()
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
        reference: object,
    ) -> GeneratedProposal:
        trace.prompts.append(prompt)
        return GeneratedProposal(_proposal(cast(Any, reference).baseline_sha))

    def evaluate(
        _root_runner: object,
        _task_spec: TaskSpec,
        _reference: object,
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


@pytest.mark.parametrize(
    "policy_case",
    ["missing", "public-sibling", "default-private-allowlist"],
)
def test_public_work_completes_without_private_sibling_reaching_any_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    policy_case: str,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    if policy_case == "public-sibling":
        _write_policy(root, private_paths=("src/private/**",))
    elif policy_case == "default-private-allowlist":
        _write_policy(
            root,
            default_access="private",
            public_paths=(_PUBLIC_PATH, _TEST_PATH),
        )
    private_file = root / _PRIVATE_PATH
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(_PRIVATE_CANARY_BYTES)
    assert private_file.read_bytes() == _PRIVATE_CANARY_BYTES

    runner = _HermeticMakeRunner(tmp_path / "baseline")
    trace = _Trace()
    outcomes = _Outcomes()
    plan = _prepare(root, runner, trace.events, project_id=policy_case)
    result = _run_public_plan(root, plan, runner, trace, outcomes, monkeypatch)
    captured = capsys.readouterr()

    assert cast(Any, result).accepted is True
    assert len(trace.prompts) == 1
    assert len(trace.calibration) == 1
    assert len(trace.evaluator_calls) == 1
    assert len(outcomes.memory) == 1
    assert len(outcomes.training) == 1
    assert any(event.startswith("SELF_IMPROVE_POLICY_LOADED ") for event in trace.events)
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
    )
    assert _PRIVATE_CANARY not in observed
    assert _PRIVATE_PATH not in observed


@pytest.mark.parametrize(
    ("private_rules", "changed_paths"),
    [
        ((_PRIVATE_PATH,), (_PRIVATE_PATH,)),
        (("src/private/**",), (_PRIVATE_PATH,)),
        (("src/private/**",), (_PUBLIC_PATH, _PRIVATE_PATH)),
    ],
    ids=("exact", "glob", "mixed-public-private"),
)
def test_private_scope_denial_stops_before_patch_prompt_provider_and_learning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    private_rules: tuple[str, ...],
    changed_paths: tuple[str, ...],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=private_rules)
    private_file = root / _PRIVATE_PATH
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(_PRIVATE_CANARY_BYTES)
    runner = _HermeticMakeRunner(
        tmp_path / "baseline",
        changed_paths=changed_paths,
        source_bytes=_PRIVATE_CANARY_BYTES,
    )
    trace = _Trace()
    outcomes = _Outcomes()
    provider_calls: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda *args: provider_calls.append(args),
    )

    with pytest.raises(SelfImprovePolicyViolation) as captured_error:
        _prepare(root, runner, trace.events, project_id="blocked-private")
    captured = capsys.readouterr()

    assert [target for target, _variables in runner.calls] == ["git-show-name-only"]
    assert not (tmp_path / "baseline").exists()
    assert provider_calls == []
    assert trace.prompts == []
    assert trace.calibration == []
    assert trace.results == []
    assert outcomes.memory == []
    assert outcomes.training == []
    assert any("reason=private_path" in event for event in trace.events)
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed
    assert _PRIVATE_PATH not in observed


def test_malformed_policy_fails_closed_before_repository_or_provider_access(
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
        _prepare(root, runner, trace.events, project_id="malformed-policy")
    captured = capsys.readouterr()

    assert runner.calls == []
    assert provider_calls == []
    assert any("reason=policy_unavailable" in event for event in trace.events)
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed


def test_policy_drift_after_approval_blocks_provider_results_and_learning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("src/private/**",))
    private_file = root / _PRIVATE_PATH
    private_file.parent.mkdir(parents=True)
    private_file.write_bytes(_PRIVATE_CANARY_BYTES)
    runner = _HermeticMakeRunner(tmp_path / "baseline")
    trace = _Trace()
    outcomes = _Outcomes()
    plan = _prepare(root, runner, trace.events, project_id="drifted-policy")
    _write_policy(root, private_paths=(_PUBLIC_PATH,))
    provider_calls: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda *args: provider_calls.append(args),
    )

    service = build_managed_self_improve_runner(
        root,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
        outcome_adapter_factory=lambda _cache: cast(ManagedOutcomeAdapter, outcomes),
        progress_sink=trace.events.append,
    )
    with pytest.raises(SelfImprovePolicyViolation) as captured_error:
        service.run(plan)
    captured = capsys.readouterr()

    assert provider_calls == []
    assert trace.prompts == []
    assert trace.calibration == []
    assert trace.results == []
    assert outcomes.memory == []
    assert outcomes.training == []
    assert any("reason=policy_drift" in event for event in trace.events)
    observed = _observable(
        trace=trace,
        outcomes=outcomes,
        runner=runner,
        stdout=captured.out + captured.err,
        errors=(captured_error.value,),
    )
    assert _PRIVATE_CANARY not in observed
    assert _PRIVATE_PATH not in observed


def test_two_projects_with_same_path_and_opposite_policies_remain_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    public_root = tmp_path / "public-project"
    private_root = tmp_path / "private-project"
    public_root.mkdir()
    private_root.mkdir()
    _write_policy(public_root, private_paths=("src/private/**",))
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
        project_id="same-path-public",
    )
    public_result = _run_public_plan(
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
            project_id="same-path-private",
        )
    captured = capsys.readouterr()

    assert cast(Any, public_result).accepted is True
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

