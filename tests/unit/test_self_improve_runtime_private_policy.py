"""Runtime privacy boundaries for self-improvement preparation and learning."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    PlanBoundProposal,
    PromptPlan,
    PromptShard,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.private_policy import (
    SelfImproveRuntimePolicyGuard,
    load_self_improve_policy,
)
from general_ludd.self_improve.runtime import (
    MakeResult,
    build_managed_self_improve_runner,
    prepare_managed_self_improve_plan,
)

PRIVATE_CANARY = "PROJECT_PRICE_FORMULA_CANARY_91f1"
PRIVATE_PATH = "src/private/price_formula.py"
PUBLIC_PATH = "src/public/api.py"
TEST_PATH = "tests/unit/test_public_api.py"


def _write_policy(
    root: Path,
    *,
    private_paths: tuple[str, ...] = ("src/private/**",),
    public_paths: tuple[str, ...] = (),
    default_access: str = "public",
) -> Path:
    policy = root / ".gludd" / "self-improve-policy.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
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
    return policy


def _task(*, test_path: str = TEST_PATH) -> TaskSpec:
    return TaskSpec(
        task_id="S83.145",
        objective="Implement one approved public repository feature.",
        canonical_make_commands=(
            f"make test-specific TESTFILE={test_path}",
        ),
    )


def _reference(*, baseline_sha: str = "a" * 40) -> CodexReference:
    return CodexReference(
        baseline_sha=baseline_sha,
        reference_sha="b" * 40,
        changed_files=frozenset({PUBLIC_PATH}),
        test_files=frozenset({TEST_PATH}),
        changed_lines=1,
        elapsed_seconds=0.1,
    )


def _prompt(source: str = "VALUE = 0\n") -> PromptPlan:
    return PromptPlan(
        shards=(PromptShard((PUBLIC_PATH,), f"L1|{source}", ((1, 2),)),),
        source_bytes=len(source.encode()),
        baseline_files=((PUBLIC_PATH, source),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )


def _proposal(*, baseline_sha: str = "a" * 40) -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": baseline_sha,
                "task_id": "S83.145",
                "edits": [
                    {
                        "operation": "replace",
                        "path": PUBLIC_PATH,
                        "old_text": "VALUE = 0\n",
                        "new_text": "VALUE = 1\n",
                    }
                ],
                "tests": [TEST_PATH],
                "make_commands": [f"make test-specific TESTFILE={TEST_PATH}"],
                "commit_message": "feat: improve public API",
            }
        )
    )


def _approve(
    root: Path,
    *,
    project_id: str = "project-runtime-policy",
    baseline_sha: str = "a" * 40,
    source: str = "VALUE = 0\n",
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id=f"approval-{project_id}",
        todo_id="todo-runtime-policy",
        project_id=project_id,
        repo_root=root,
        task=_task(),
        reference=_reference(baseline_sha=baseline_sha),
        prompt=_prompt(source),
        required_output_tokens=512,
        max_attempts=1,
    )


class _PreparationRunner:
    """Make-only preparation double with optional policy drift after path lookup."""

    def __init__(
        self,
        context_root: Path,
        changed_path: str,
        *,
        drift: Callable[[], object] | None = None,
        include_test: bool = True,
        alias_public_to_private: bool = False,
    ) -> None:
        self.context_root = context_root
        self.changed_path = changed_path
        self.drift = drift
        self.include_test = include_test
        self.alias_public_to_private = alias_public_to_private
        self.calls: list[str] = []

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
            output = f"{self.changed_path}\n"
            if self.include_test:
                output += f"{TEST_PATH}\n"
            if self.drift is not None:
                self.drift()
        elif target == "git-show-full":
            output = f"-old\n+{PRIVATE_CANARY}\n"
        elif target == "agent-worktree-base":
            source = self.context_root / self.changed_path
            if self.alias_public_to_private:
                private_source = self.context_root / "src/private/api.py"
                private_source.parent.mkdir(parents=True, exist_ok=True)
                private_source.write_text(PRIVATE_CANARY, encoding="utf-8")
                source.parent.parent.mkdir(parents=True, exist_ok=True)
                source.parent.symlink_to(private_source.parent, target_is_directory=True)
            else:
                source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(
                PRIVATE_CANARY if self.changed_path == PRIVATE_PATH else "VALUE = 0\n",
                encoding="utf-8",
            )
            test = self.context_root / TEST_PATH
            test.parent.mkdir(parents=True, exist_ok=True)
            test.write_text("assert True\n", encoding="utf-8")
            output = f"WORKTREE_PATH={self.context_root}\n"
        return MakeResult(("make", target), 0, output, "", 0.01)

    def run_command(self, command: str, *, timeout: int = 900) -> MakeResult:
        del command, timeout
        raise AssertionError("non-mechanical preparation ran a command")

    def run_observable(
        self,
        target: str,
        variables: dict[str, str],
        *,
        timeout: int,
    ) -> MakeResult:
        del target, variables, timeout
        raise AssertionError("plan preparation invoked a provider")


@pytest.mark.parametrize("invalid_kind", ["malformed", "symlink"])
def test_invalid_policy_blocks_before_reference_or_provider_and_emits_no_secret(
    tmp_path: Path,
    invalid_kind: str,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    policy = _write_policy(root)
    if invalid_kind == "malformed":
        policy.write_text("{" + PRIVATE_CANARY, encoding="utf-8")
    else:
        replacement = tmp_path / f"replacement-{PRIVATE_CANARY}.json"
        replacement.write_text(policy.read_text(encoding="utf-8"), encoding="utf-8")
        policy.unlink()
        policy.symlink_to(replacement)
    runner = _PreparationRunner(tmp_path / "context", PRIVATE_PATH)
    events: list[str] = []

    with pytest.raises(SelfImprovePolicyViolation) as captured:
        prepare_managed_self_improve_plan(
            root,
            approval_id="approval-invalid",
            todo_id="todo-invalid",
            project_id="project-invalid",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=1,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
            progress_sink=events.append,
        )

    observable = json.dumps({"error": str(captured.value), "events": events})
    assert runner.calls == []
    assert "SELF_IMPROVE_POLICY_BLOCKED" in observable
    assert "reason=policy_unavailable" in observable
    assert PRIVATE_CANARY not in observable
    assert PRIVATE_PATH not in observable


def test_private_reference_is_rejected_before_full_patch_or_context_read(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root)
    runner = _PreparationRunner(tmp_path / "context", PRIVATE_PATH)
    events: list[str] = []

    with pytest.raises(SelfImprovePolicyViolation) as captured:
        prepare_managed_self_improve_plan(
            root,
            approval_id="approval-private",
            todo_id="todo-private",
            project_id="project-private",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=1,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
            progress_sink=events.append,
        )

    observable = json.dumps({"error": str(captured.value), "events": events})
    assert runner.calls == ["git-show-name-only"]
    assert "git-show-full" not in runner.calls
    assert "agent-worktree-base" not in runner.calls
    assert "reason=private_path" in observable
    assert PRIVATE_CANARY not in observable
    assert PRIVATE_PATH not in observable


def test_policy_drift_after_path_discovery_blocks_before_patch_materialization(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("unrelated/**",))
    runner = _PreparationRunner(
        tmp_path / "context",
        PUBLIC_PATH,
        drift=lambda: _write_policy(root, private_paths=(PUBLIC_PATH,)),
    )
    events: list[str] = []

    with pytest.raises(SelfImprovePolicyViolation):
        prepare_managed_self_improve_plan(
            root,
            approval_id="approval-drift",
            todo_id="todo-drift",
            project_id="project-drift",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=1,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
            progress_sink=events.append,
        )

    assert runner.calls == ["git-show-name-only"]
    assert any("reason=policy_drift" in event for event in events)
    assert PRIVATE_CANARY not in json.dumps(events)
    assert PUBLIC_PATH not in json.dumps(events)


def test_private_fallback_test_command_blocks_before_context_or_prompt(
    tmp_path: Path,
) -> None:
    private_test = "tests/private/test_price_formula.py"
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("tests/private/**",))
    runner = _PreparationRunner(
        tmp_path / "context",
        PUBLIC_PATH,
        include_test=False,
    )
    events: list[str] = []

    with pytest.raises(SelfImprovePolicyViolation):
        prepare_managed_self_improve_plan(
            root,
            approval_id="approval-private-test",
            todo_id="todo-private-test",
            project_id="project-private-test",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(test_path=private_test),
            max_attempts=1,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
            progress_sink=events.append,
        )

    assert runner.calls == ["git-show-name-only", "git-show-full"]
    assert any("reason=private_path" in event for event in events)
    assert private_test not in json.dumps(events)
    assert PRIVATE_CANARY not in json.dumps(events)


def test_public_lexical_path_cannot_follow_baseline_symlink_before_prompt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root)
    runner = _PreparationRunner(
        tmp_path / "context",
        PUBLIC_PATH,
        alias_public_to_private=True,
    )
    events: list[str] = []

    with pytest.raises(SelfImprovePolicyViolation):
        prepare_managed_self_improve_plan(
            root,
            approval_id="approval-context-symlink",
            todo_id="todo-context-symlink",
            project_id="project-context-symlink",
            baseline_ref="a" * 40,
            reference_ref="b" * 40,
            task=_task(),
            max_attempts=1,
            root_runner=runner,
            make_runner_factory=lambda _path: runner,
            progress_sink=events.append,
        )

    assert runner.calls == [
        "git-show-name-only",
        "git-show-full",
        "agent-worktree-base",
        "agent-cleanup",
    ]
    assert any("reason=private_path" in event for event in events)
    assert PRIVATE_CANARY not in json.dumps(events)
    assert PUBLIC_PATH not in json.dumps(events)


def test_missing_policy_keeps_public_preparation_compatible_and_observable(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    runner = _PreparationRunner(tmp_path / "context", PUBLIC_PATH)
    events: list[str] = []

    plan = prepare_managed_self_improve_plan(
        root,
        approval_id="approval-public",
        todo_id="todo-public",
        project_id="project-public",
        baseline_ref="a" * 40,
        reference_ref="b" * 40,
        task=_task(),
        max_attempts=1,
        root_runner=runner,
        make_runner_factory=lambda _path: runner,
        progress_sink=events.append,
    )

    assert plan.policy_digest == load_self_improve_policy(root).digest
    assert runner.calls == [
        "git-show-name-only",
        "git-show-full",
        "agent-worktree-base",
        "agent-cleanup",
    ]
    policy_events = [
        event for event in events if event.startswith("SELF_IMPROVE_POLICY_LOADED ")
    ]
    assert policy_events
    assert all("reason=matched" in event for event in policy_events)
    assert PUBLIC_PATH not in json.dumps(events)


def test_runtime_evaluator_reloads_policy_before_apply_boundary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    policy_path = _write_policy(root, private_paths=("unrelated/**",))
    plan = _approve(root)
    evaluated: list[object] = []
    events: list[str] = []

    def evaluate(*args: object, **kwargs: object) -> AttemptResult:
        evaluated.append((args, kwargs))
        raise AssertionError("drifted policy reached proposal application")

    service = build_managed_self_improve_runner(
        root,
        root_runner=cast(Any, object()),
        attempt_evaluator=cast(Any, evaluate),
        progress_sink=events.append,
    )
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_access": "public",
                "private_paths": [PUBLIC_PATH],
                "public_paths": [],
            }
        ),
        encoding="utf-8",
    )
    bound = PlanBoundProposal(
        _proposal(),
        plan.attempt_identity_digest,
        plan.policy_digest,
    )

    with pytest.raises(SelfImprovePolicyViolation):
        service.attempt_evaluator(
            plan.task,
            plan.reference,
            bound,
            1,
            expected_attempt_identity_digest=plan.attempt_identity_digest,
            merge=False,
        )

    assert evaluated == []
    assert any("reason=policy_drift" in event for event in events)
    assert PRIVATE_CANARY not in json.dumps(events)
    assert PUBLIC_PATH not in json.dumps(events)


class _Reservation:
    def mark_eligible(self, _identity: object) -> None:
        return None

    def mark_failed(self, _identity: object) -> None:
        return None


class _ModelManager:
    def __init__(self, cache_root: Path, **_kwargs: object) -> None:
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
            path=Path("/tmp/gludd-runtime-private-policy.gguf"),
            model_id="qwen2.5-coder-0.5b",
            source="owned",
            resolved_revision="c" * 40,
            artifact_sha256="d" * 64,
            lease_path=Path("/tmp/gludd-runtime-private-policy.lease"),
        )


class _Outcomes:
    planner_store = object()

    def __init__(self) -> None:
        self.loads: list[dict[str, object]] = []
        self.records: list[dict[str, object]] = []

    def load_failed_model_ids(self, **kwargs: object) -> tuple[str, ...]:
        self.loads.append(kwargs)
        return ()

    def record_outcome(self, **kwargs: object) -> int:
        self.records.append(kwargs)
        return len(self.records)


def _candidate() -> PlannedModelCandidate:
    model = get_model("qwen2.5-coder-0.5b")
    assert model is not None
    return PlannedModelCandidate(model, "c" * 40, 0.5, 0)


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
            changed_files=frozenset({PUBLIC_PATH}),
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
            elapsed_seconds=0.1,
            changed_lines=1,
        ),
        patch_equivalence="equivalent",
        proposal=bound.proposal,
        diagnostics="",
        attempt_identity_digest=bound.attempt_identity_digest,
    )


def test_learning_identity_binds_project_baseline_and_policy_without_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("unrelated/**",))
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outcomes = _Outcomes()
    events: list[str] = []
    monkeypatch.setattr(
        runtime_module,
        "ModelLeaseManager",
        lambda **kwargs: _ModelManager(cache_root, **kwargs),
    )
    monkeypatch.setattr(
        runtime_module,
        "plan_model_candidates",
        lambda *_args, **_kwargs: (_candidate(),),
    )
    monkeypatch.setattr(runtime_module, "unified_probe", lambda: object())
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda _runner, _model, _prompt_plan, _task_spec, reference: GeneratedProposal(
            _proposal(baseline_sha=reference.baseline_sha)
        ),
    )

    def evaluate(
        _root_runner: object,
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
        return _accepted(bound)

    service = build_managed_self_improve_runner(
        root,
        root_runner=cast(Any, object()),
        attempt_evaluator=cast(Any, evaluate),
        outcome_adapter_factory=lambda _cache: cast(ManagedOutcomeAdapter, outcomes),
        progress_sink=events.append,
    )
    first = _approve(root, project_id="project-one", source=PRIVATE_CANARY)
    service.run(first)
    second = _approve(root, project_id="project-two", source=PRIVATE_CANARY)
    service.run(second)
    _write_policy(root, private_paths=("another-private/**",))
    third = _approve(root, project_id="project-two", source=PRIVATE_CANARY)
    service.run(third)
    fourth = _approve(
        root,
        project_id="project-two",
        baseline_sha="e" * 40,
        source=PRIVATE_CANARY,
    )
    service.run(fourth)

    recorded_identities = {
        cast(str, record["attempt_identity_digest"]) for record in outcomes.records
    }
    loaded_identities = {
        cast(str, record["attempt_identity_digest"]) for record in outcomes.loads
    }
    assert len(outcomes.records) == 4
    assert len(recorded_identities) == 4
    assert loaded_identities == recorded_identities
    assert all(identity != first.attempt_identity_digest for identity in recorded_identities)
    observable = json.dumps(
        {"events": events, "loads": outcomes.loads, "records": outcomes.records},
        default=str,
    )
    assert PRIVATE_CANARY not in observable
    assert PUBLIC_PATH not in json.dumps(events)


def test_inverse_project_policies_isolate_shared_failure_training_canary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    public_default_root = tmp_path / "public-default"
    private_default_root = tmp_path / "private-default"
    public_default_root.mkdir()
    private_default_root.mkdir()
    _write_policy(public_default_root, private_paths=("src/private/**",))
    _write_policy(
        private_default_root,
        private_paths=(),
        public_paths=(PUBLIC_PATH, TEST_PATH),
        default_access="private",
    )
    cache_root = tmp_path / "shared-cache"
    cache_root.mkdir()
    planner_failures: list[tuple[str, ...]] = []

    class _SharedOutcomes(_Outcomes):
        def __init__(self) -> None:
            super().__init__()
            self.failures: dict[str, tuple[str, ...]] = {}

        def load_failed_model_ids(self, **kwargs: object) -> tuple[str, ...]:
            self.loads.append(kwargs)
            identity = cast(str, kwargs["attempt_identity_digest"])
            return self.failures.get(identity, ())

    outcomes = _SharedOutcomes()
    monkeypatch.setattr(
        runtime_module,
        "ModelLeaseManager",
        lambda **kwargs: _ModelManager(cache_root, **kwargs),
    )

    def plan_candidates(*args: object, **_kwargs: object) -> tuple[PlannedModelCandidate, ...]:
        planner_failures.append(cast(tuple[str, ...], args[2]))
        return (_candidate(),)

    monkeypatch.setattr(runtime_module, "plan_model_candidates", plan_candidates)
    monkeypatch.setattr(runtime_module, "unified_probe", lambda: object())
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda _runner, _model, _prompt_plan, _task_spec, reference: GeneratedProposal(
            _proposal(baseline_sha=reference.baseline_sha)
        ),
    )

    def evaluate(
        _root_runner: object,
        _task_spec: TaskSpec,
        _reference_spec: CodexReference,
        bound: PlanBoundProposal,
        _attempt: int,
        **_kwargs: object,
    ) -> AttemptResult:
        return _accepted(bound)

    for root, project_id in (
        (public_default_root, "denylist-project"),
        (private_default_root, "allowlist-project"),
    ):
        service = build_managed_self_improve_runner(
            root,
            root_runner=cast(Any, object()),
            attempt_evaluator=cast(Any, evaluate),
            outcome_adapter_factory=lambda _cache: cast(ManagedOutcomeAdapter, outcomes),
            progress_sink=lambda _event: None,
        )
        service.run(_approve(root, project_id=project_id))
        if len(outcomes.loads) == 1:
            first_identity = cast(str, outcomes.loads[0]["attempt_identity_digest"])
            outcomes.failures[first_identity] = (PRIVATE_CANARY,)

    identities = [
        cast(str, load["attempt_identity_digest"]) for load in outcomes.loads
    ]
    assert len(identities) == 2
    assert identities[0] != identities[1]
    assert planner_failures == [(), ()]
    assert PRIVATE_CANARY not in json.dumps(outcomes.records, default=str)


def test_inverse_project_policy_blocks_provider_and_learning_canary_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("unrelated/**",))
    public_plan = _approve(root, source=PRIVATE_CANARY)
    _write_policy(root, private_paths=(PUBLIC_PATH,))
    provider_calls: list[object] = []
    outcome_calls: list[object] = []
    monkeypatch.setattr(
        runtime_module,
        "_generate_local_proposal_plan_result",
        lambda *_args: provider_calls.append(_args),
    )

    with pytest.raises(SelfImprovePolicyViolation) as captured:
        _approve(root, project_id="inverse-project", source=PRIVATE_CANARY)

    events: list[str] = []
    service = build_managed_self_improve_runner(
        root,
        root_runner=cast(Any, object()),
        outcome_adapter_factory=cast(
            Any,
            lambda _cache: outcome_calls.append(_cache),
        ),
        progress_sink=events.append,
    )
    with pytest.raises(SelfImprovePolicyViolation):
        service.run(public_plan)

    observable = json.dumps(
        {
            "approval_error": str(captured.value),
            "events": events,
            "outcomes": outcome_calls,
        },
        default=str,
    )
    assert provider_calls == []
    assert outcome_calls == []
    assert PRIVATE_CANARY not in observable
    assert PUBLIC_PATH not in observable


def test_learning_guard_detects_drift_during_failure_load_and_skips_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_policy(root, private_paths=("unrelated/**",))
    plan = _approve(root)
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    events: list[str] = []
    provider_calls: list[object] = []

    class _DriftingOutcomes(_Outcomes):
        def load_failed_model_ids(self, **kwargs: object) -> tuple[str, ...]:
            self.loads.append(kwargs)
            _write_policy(root, private_paths=(PUBLIC_PATH,))
            return ()

    outcomes = _DriftingOutcomes()
    monkeypatch.setattr(
        runtime_module,
        "ModelLeaseManager",
        lambda **kwargs: _ModelManager(cache_root, **kwargs),
    )
    monkeypatch.setattr(
        runtime_module,
        "plan_model_candidates",
        lambda *_args, **_kwargs: provider_calls.append((_args, _kwargs)),
    )
    monkeypatch.setattr(runtime_module, "unified_probe", lambda: object())
    service = build_managed_self_improve_runner(
        root,
        root_runner=cast(Any, object()),
        outcome_adapter_factory=lambda _cache: cast(ManagedOutcomeAdapter, outcomes),
        progress_sink=events.append,
    )

    with pytest.raises(SelfImprovePolicyViolation):
        service.run(plan)

    assert len(outcomes.loads) == 1
    assert outcomes.records == []
    assert provider_calls == []
    assert any(event.startswith("SELF_IMPROVE_LEARNING_SKIPPED ") for event in events)
    assert any("reason=policy_drift" in event for event in events)
    assert PRIVATE_CANARY not in json.dumps(events)
    assert PUBLIC_PATH not in json.dumps(events)


def test_default_learning_cache_is_namespaced_by_safe_scope_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths: list[str] = []

    class _Store:
        def __init__(self, path: str) -> None:
            paths.append(path)

    monkeypatch.setattr(runtime_module, "CapabilityEvidenceStore", _Store)

    runtime_module._default_runtime_outcome_adapter(
        tmp_path,
        scope_digest="1" * 64,
    )
    runtime_module._default_runtime_outcome_adapter(
        tmp_path,
        scope_digest="2" * 64,
    )

    assert len(paths) == 2
    assert paths[0] != paths[1]
    assert "1" * 64 in paths[0]
    assert "2" * 64 in paths[1]
    assert PRIVATE_CANARY not in json.dumps(paths)


def test_learning_task_drift_event_sanitizes_untrusted_policy_digest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    events: list[str] = []
    guard = SelfImproveRuntimePolicyGuard.bound(
        root,
        PRIVATE_CANARY,
        events.append,
        SelfImprovePolicyViolation,
    )

    with pytest.raises(SelfImprovePolicyViolation):
        guard.require_learning(
            "drifted task",
            hashlib.sha256(b"approved task").hexdigest(),
            (),
        )

    assert any("reason=task_drift" in event for event in events)
    assert PRIVATE_CANARY not in json.dumps(events)
