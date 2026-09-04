"""Managed-runner enforcement for project-private self-improvement paths."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

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
    ManagedSelfImproveRunner,
    PlanBoundProposal,
    PromptPlan,
    PromptShard,
    TaskSpec,
)
from general_ludd.self_improve.model_candidate_planner import PlannedModelCandidate
from general_ludd.self_improve.private_policy import load_self_improve_policy

PRIVATE_CANARY = "PRIVATE_PRICE_FORMULA_CANARY_7b21"
PRIVATE_PATH = "src/business/pricing.py"
PUBLIC_PATH = "src/public/api.py"
PRIVATE_TEST_PATH = "tests/private/test_pricing.py"


def _write_policy(
    root: Path,
    *,
    private_paths: tuple[str, ...] = ("src/business/**", "tests/private/**"),
    public_paths: tuple[str, ...] = (),
    default_access: str = "public",
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
        objective="Improve one approved repository file.",
        canonical_make_commands=(
            "make test-specific TESTFILE=tests/unit/test_public.py",
        ),
    )


def _reference(
    *,
    changed_files: frozenset[str] = frozenset({PUBLIC_PATH}),
    test_files: frozenset[str] = frozenset({"tests/unit/test_public.py"}),
) -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="b" * 40,
        changed_files=changed_files,
        test_files=test_files,
        changed_lines=1,
        elapsed_seconds=0.5,
    )


def _prompt(path: str = PUBLIC_PATH, source: str = "return 0\n") -> PromptPlan:
    return PromptPlan(
        shards=(PromptShard((path,), f"L1|{source}", ((1, 2),)),),
        source_bytes=len(source.encode()),
        baseline_files=((path, source),),
        proposal_protocol="self-improve-compact-proposal-v4",
    )


def _proposal(
    *,
    path: str = PUBLIC_PATH,
    operation: str = "replace",
    old_text: str = "return 0\n",
    new_text: str = "return 1\n",
    tests: tuple[str, ...] = ("tests/unit/test_public.py",),
) -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.145",
                "edits": [
                    {
                        "operation": operation,
                        "path": path,
                        "old_text": old_text,
                        "new_text": new_text,
                    }
                ],
                "tests": list(tests),
                "make_commands": [
                    "make test-specific TESTFILE=tests/unit/test_public.py"
                ],
                "commit_message": "feat: improve public behavior",
            }
        )
    )


def _approve(
    root: Path,
    *,
    prompt: PromptPlan | str | None = None,
    reference: CodexReference | None = None,
    mechanical_proposal: ProposalManifest | None = None,
    max_attempts: int = 2,
) -> ApprovedSelfImprovePlan:
    return ApprovedSelfImprovePlan.approve(
        approval_id="approval-private-policy",
        todo_id="todo-private-policy",
        project_id="project-private-policy",
        repo_root=root,
        task=_task(),
        reference=reference or _reference(),
        prompt=prompt or _prompt(),
        required_output_tokens=512,
        max_attempts=max_attempts,
        mechanical_proposal=mechanical_proposal,
    )


def _attempt_result(bound: PlanBoundProposal) -> AttemptResult:
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


class _MechanicalRunner:
    def __init__(self, progress: list[str], effects: list[str]) -> None:
        self._progress = progress
        self._effects = effects

    def service(self) -> ManagedSelfImproveRunner:
        def forbidden_generator(*_args: object) -> ProposalManifest:
            self._effects.append("provider")
            raise AssertionError("mechanical proposal called provider")

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
            assert bound.policy_digest
            assert merge is False
            self._effects.append("evaluate")
            return _attempt_result(bound)

        return ManagedSelfImproveRunner(
            proposal_generator=cast(Any, forbidden_generator),
            attempt_evaluator=cast(Any, evaluate),
            progress_sink=self._progress.append,
        )


class _Reservation:
    def mark_eligible(self, _identity: object) -> None:
        return None

    def mark_failed(self, _identity: object) -> None:
        return None


class _ModelManager:
    cache_root = Path("/tmp/gludd-private-policy-model-cache")

    def __init__(self) -> None:
        self.acquisitions = 0

    def resolve_revision(self, _repo_id: str) -> str:
        return "c" * 40

    def owned_identities_for_model_ids(
        self, _model_ids: tuple[str, ...]
    ) -> tuple[object, ...]:
        return ()

    @contextmanager
    def reserve_plan(self, *_args: object, **_kwargs: object) -> Iterator[_Reservation]:
        yield _Reservation()

    @contextmanager
    def acquire(self, *_args: object, **_kwargs: object) -> Iterator[Any]:
        self.acquisitions += 1
        yield SimpleNamespace(
            path=Path("/tmp/gludd-private-policy-model.gguf"),
            model_id="qwen2.5-coder-0.5b",
            source="owned",
            resolved_revision="c" * 40,
            artifact_sha256="d" * 64,
        )


class _Outcomes:
    planner_store = object()

    def __init__(self) -> None:
        self.records: list[object] = []

    def load_failed_model_ids(self, **_kwargs: object) -> tuple[str, ...]:
        return ()

    def record_outcome(self, **kwargs: object) -> int:
        self.records.append(kwargs)
        return len(self.records)


def _candidate() -> PlannedModelCandidate:
    model = get_model("qwen2.5-coder-0.5b")
    assert model is not None
    return PlannedModelCandidate(model, "c" * 40, 0.5, 0)


def test_approved_plan_binds_canonical_policy_into_plan_and_proposal_identities(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    policy = load_self_improve_policy(tmp_path)
    plan = _approve(tmp_path)
    payload = json.loads(plan.to_json())

    assert plan.policy_digest == policy.digest
    assert payload["policy_digest"] == policy.digest
    assert PlanBoundProposal(
        _proposal(), plan.attempt_identity_digest, plan.policy_digest
    ).policy_digest == policy.digest

    _write_policy(tmp_path, private_paths=("other/**",))
    replacement = _approve(tmp_path)
    assert replacement.policy_digest != plan.policy_digest
    assert replacement.identity_digest != plan.identity_digest
    assert replacement.attempt_identity_digest == plan.attempt_identity_digest


@pytest.mark.parametrize(
    ("operation", "old_text", "new_text"),
    [
        ("create", "", PRIVATE_CANARY),
        ("replace", "return 0\n", PRIVATE_CANARY),
        ("delete", PRIVATE_CANARY, ""),
    ],
)
def test_private_mechanical_edit_operations_fail_before_any_side_effect_and_do_not_leak(
    tmp_path: Path,
    operation: str,
    old_text: str,
    new_text: str,
) -> None:
    _write_policy(tmp_path)
    progress: list[str] = []
    effects: list[str] = []
    proposal = _proposal(
        path=PRIVATE_PATH,
        operation=operation,
        old_text=old_text,
        new_text=new_text,
    )

    with pytest.raises(ValueError) as captured:
        plan = _approve(tmp_path, mechanical_proposal=proposal)
        _MechanicalRunner(progress, effects).service().run(plan)

    observable = json.dumps({"error": str(captured.value), "progress": progress})
    assert effects == []
    assert PRIVATE_PATH not in observable
    assert PRIVATE_CANARY not in observable


def test_private_prompt_baseline_and_private_test_reference_fail_before_provider(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    for prompt, reference in (
        (_prompt(PRIVATE_PATH, PRIVATE_CANARY), _reference()),
        (_prompt(), _reference(test_files=frozenset({PRIVATE_TEST_PATH}))),
    ):
        with pytest.raises(ValueError) as captured:
            _approve(tmp_path, prompt=prompt, reference=reference)
        assert PRIVATE_CANARY not in str(captured.value)
        assert PRIVATE_PATH not in str(captured.value)
        assert PRIVATE_TEST_PATH not in str(captured.value)


def test_private_proposal_test_path_is_rejected_before_mechanical_evaluation(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    proposal = _proposal(tests=(PRIVATE_TEST_PATH,))

    with pytest.raises(ValueError) as captured:
        _approve(tmp_path, mechanical_proposal=proposal)

    assert PRIVATE_TEST_PATH not in str(captured.value)
    assert PRIVATE_CANARY not in str(captured.value)


def test_serialized_policy_digest_tampering_invalidates_human_approval(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    payload = json.loads(_approve(tmp_path).to_json())
    payload["policy_digest"] = "f" * 64

    with pytest.raises(ValueError, match="approved plan identity"):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))

    payload["policy_digest"] = "not-a-digest"
    with pytest.raises(ValueError, match="policy_digest"):
        ApprovedSelfImprovePlan.from_json(json.dumps(payload))


def test_malformed_policy_at_approval_is_fixed_message_without_exception_chain(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path)
    policy_path.write_text("{" + PRIVATE_CANARY, encoding="utf-8")

    with pytest.raises(ValueError) as captured:
        _approve(tmp_path)

    assert captured.value.__cause__ is None
    assert str(captured.value) == "self-improvement blocked by project privacy policy"
    assert PRIVATE_CANARY not in str(captured.value)


def test_policy_drift_before_run_blocks_without_provider_evaluator_or_raw_paths(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, private_paths=("unrelated/**",))
    plan = _approve(tmp_path, mechanical_proposal=_proposal())
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
    progress: list[str] = []
    effects: list[str] = []

    with pytest.raises(ValueError) as captured:
        _MechanicalRunner(progress, effects).service().run(plan)

    observable = json.dumps({"error": str(captured.value), "progress": progress})
    assert effects == []
    assert PUBLIC_PATH not in observable
    assert PRIVATE_CANARY not in observable
    assert "SELF_IMPROVE_POLICY_BLOCKED" in observable
    assert plan.policy_digest in observable


def test_policy_symlink_substitution_fails_closed_without_disclosing_target(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, private_paths=("unrelated/**",))
    plan = _approve(tmp_path, mechanical_proposal=_proposal())
    replacement = tmp_path / "replacement-policy.json"
    replacement.write_text(policy_path.read_text(encoding="utf-8"), encoding="utf-8")
    policy_path.unlink()
    policy_path.symlink_to(replacement)
    progress: list[str] = []
    effects: list[str] = []

    with pytest.raises(ValueError) as captured:
        _MechanicalRunner(progress, effects).service().run(plan)

    observable = json.dumps({"error": str(captured.value), "progress": progress})
    assert effects == []
    assert str(replacement) not in observable
    assert PRIVATE_CANARY not in observable
    assert "SELF_IMPROVE_POLICY_BLOCKED" in observable


def test_malformed_policy_after_approval_emits_only_safe_block_evidence(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, private_paths=())
    plan = _approve(tmp_path, mechanical_proposal=_proposal())
    policy_path.write_text("{" + PRIVATE_CANARY, encoding="utf-8")
    progress: list[str] = []
    effects: list[str] = []

    with pytest.raises(ValueError) as captured:
        _MechanicalRunner(progress, effects).service().run(plan)

    observable = json.dumps({"error": str(captured.value), "progress": progress})
    assert effects == []
    assert captured.value.__cause__ is None
    assert PRIVATE_CANARY not in observable
    assert "SELF_IMPROVE_POLICY_BLOCKED" in observable


def test_generated_private_proposal_is_never_evaluated_learned_or_retried(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    plan = _approve(tmp_path, max_attempts=2)
    manager = _ModelManager()
    outcomes = _Outcomes()
    provider_calls: list[tuple[object, ...]] = []
    evaluated: list[object] = []
    progress: list[str] = []

    def generate(*args: object) -> ProposalManifest:
        provider_calls.append(args)
        return _proposal(path=PRIVATE_PATH, new_text=PRIVATE_CANARY)

    def evaluate(*args: object, **_kwargs: object) -> AttemptResult:
        evaluated.append(args)
        raise AssertionError("private proposal reached evaluator")

    service = ManagedSelfImproveRunner(
        proposal_generator=cast(Any, generate),
        attempt_evaluator=cast(Any, evaluate),
        model_manager_factory=cast(Any, lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _cache: outcomes,
        candidate_planner=cast(Any, lambda *_args, **_kwargs: (_candidate(),)),
        hardware_probe=cast(Any, lambda: object()),
        progress_sink=progress.append,
    )

    with pytest.raises(ValueError) as captured:
        service.run(plan)

    observable = json.dumps({"error": str(captured.value), "progress": progress})
    assert len(provider_calls) == 1
    assert PRIVATE_CANARY not in repr(provider_calls)
    assert evaluated == []
    assert manager.acquisitions == 1
    assert outcomes.records == []
    assert PRIVATE_PATH not in observable
    assert PRIVATE_CANARY not in observable
    assert "SELF_IMPROVE_POLICY_BLOCKED" in observable


def test_policy_reloads_after_generation_and_before_evaluator_staging(
    tmp_path: Path,
) -> None:
    policy_path = _write_policy(tmp_path, private_paths=("unrelated/**",))
    plan = _approve(tmp_path, max_attempts=2)
    manager = _ModelManager()
    outcomes = _Outcomes()
    evaluated: list[object] = []
    progress: list[str] = []

    def generate(*_args: object) -> ProposalManifest:
        _write_policy(tmp_path, private_paths=(PUBLIC_PATH,))
        return _proposal(new_text=PRIVATE_CANARY)

    def evaluate(*args: object, **_kwargs: object) -> AttemptResult:
        evaluated.append(args)
        raise AssertionError("policy drift reached evaluator")

    service = ManagedSelfImproveRunner(
        proposal_generator=cast(Any, generate),
        attempt_evaluator=cast(Any, evaluate),
        model_manager_factory=cast(Any, lambda **_kwargs: manager),
        outcome_adapter_factory=lambda _cache: outcomes,
        candidate_planner=cast(Any, lambda *_args, **_kwargs: (_candidate(),)),
        hardware_probe=cast(Any, lambda: object()),
        progress_sink=progress.append,
    )

    with pytest.raises(ValueError) as captured:
        service.run(plan)

    assert policy_path.is_file()
    assert evaluated == []
    assert outcomes.records == []
    assert PRIVATE_CANARY not in str(captured.value)
    assert PRIVATE_CANARY not in json.dumps(progress)


def test_symlinked_public_proposal_cannot_alias_a_private_destination(
    tmp_path: Path,
) -> None:
    _write_policy(tmp_path)
    private_directory = tmp_path / "src" / "business"
    private_directory.mkdir(parents=True)
    public_directory = tmp_path / "src" / "public"
    public_directory.parent.mkdir(parents=True, exist_ok=True)
    public_directory.symlink_to(private_directory, target_is_directory=True)
    progress: list[str] = []
    effects: list[str] = []

    with pytest.raises(ValueError) as captured:
        _approve(
            tmp_path,
            mechanical_proposal=_proposal(path=PUBLIC_PATH, operation="create", old_text=""),
        )

    assert effects == []
    assert PUBLIC_PATH not in str(captured.value)
    assert PRIVATE_PATH not in str(captured.value)
    assert PRIVATE_CANARY not in json.dumps(progress)


def test_legacy_plan_runs_only_with_canonical_empty_public_policy(tmp_path: Path) -> None:
    proposal = _proposal()
    legacy = ApprovedSelfImprovePlan(
        approval_id="legacy-approval",
        todo_id="legacy-todo",
        project_id="legacy-project",
        repo_root=tmp_path,
        task=_task(),
        reference=_reference(),
        prompt="legacy bounded public prompt",
        required_output_tokens=128,
        max_attempts=1,
        approved=True,
        mechanical_proposal=proposal,
        _schema_version=1,
    )
    progress: list[str] = []
    effects: list[str] = []

    result = _MechanicalRunner(progress, effects).service().run(legacy)
    assert result.accepted is True
    assert effects == ["evaluate"]

    _write_policy(tmp_path, private_paths=("private/**",))
    blocked_effects: list[str] = []
    with pytest.raises(ValueError) as captured:
        _MechanicalRunner(progress, blocked_effects).service().run(legacy)
    assert blocked_effects == []
    assert PRIVATE_CANARY not in str(captured.value)


def test_default_private_policy_allows_only_explicit_public_sibling(tmp_path: Path) -> None:
    _write_policy(
        tmp_path,
        private_paths=(),
        public_paths=(PUBLIC_PATH, "tests/unit/**"),
        default_access="private",
    )
    plan = _approve(tmp_path, mechanical_proposal=_proposal())
    progress: list[str] = []
    effects: list[str] = []

    result = _MechanicalRunner(progress, effects).service().run(plan)

    assert result.accepted is True
    assert effects == ["evaluate"]
    assert PRIVATE_CANARY not in json.dumps(progress)


def test_unmaterialized_execution_repository_blocks_before_all_effects(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "future-worker-checkout"
    plan = _approve(missing_root, mechanical_proposal=_proposal())
    progress: list[str] = []
    effects: list[str] = []

    with pytest.raises(ValueError) as captured:
        _MechanicalRunner(progress, effects).service().run(plan)

    assert effects == []
    assert str(missing_root) not in str(captured.value)
    assert "SELF_IMPROVE_POLICY_BLOCKED" in json.dumps(progress)


def test_public_sibling_succeeds_with_digest_only_policy_telemetry(tmp_path: Path) -> None:
    _write_policy(tmp_path)
    plan = _approve(tmp_path, mechanical_proposal=_proposal())
    progress: list[str] = []
    effects: list[str] = []

    result = _MechanicalRunner(progress, effects).service().run(plan)

    observable = "\n".join(progress)
    assert result.accepted is True
    assert effects == ["evaluate"]
    assert "SELF_IMPROVE_POLICY_LOADED" in observable
    assert plan.policy_digest in observable
    assert PUBLIC_PATH not in observable
    assert PRIVATE_PATH not in observable
    assert PRIVATE_CANARY not in observable
    assert "allowed_count=" in observable
    assert "blocked_count=0" in observable
