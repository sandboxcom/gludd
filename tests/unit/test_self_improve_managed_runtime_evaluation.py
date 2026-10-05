"""Policy-bound managed proposal evaluation tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from general_ludd.self_improve.codex_comparison import (
    CodexReference,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import (
    AttemptResult,
    PlanBoundProposal,
    SelfImprovePolicyViolation,
    TaskSpec,
)
from general_ludd.self_improve.managed_runtime_evaluation import (
    evaluate_policy_bound_managed_proposal,
)
from general_ludd.self_improve.private_policy import load_self_improve_policy


def _proposal() -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.157",
                "edits": [
                    {
                        "operation": "replace",
                        "path": "src/public.py",
                        "old_text": "VALUE = 0\n",
                        "new_text": "VALUE = 1\n",
                    }
                ],
                "tests": ["tests/unit/test_public.py"],
                "make_commands": [
                    "make test-specific TESTFILE=tests/unit/test_public.py"
                ],
                "commit_message": "feat: improve public behavior",
            }
        )
    )


def _bound(root: Path) -> PlanBoundProposal:
    return PlanBoundProposal(
        _proposal(),
        "b" * 64,
        load_self_improve_policy(root).digest,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        task_id="S83.157",
        objective="Improve one approved public file.",
        canonical_make_commands=(
            "make test-specific TESTFILE=tests/unit/test_public.py",
        ),
    )


def _reference() -> CodexReference:
    return CodexReference(
        baseline_sha="a" * 40,
        reference_sha="c" * 40,
        changed_files=frozenset({"src/public.py"}),
        test_files=frozenset({"tests/unit/test_public.py"}),
        changed_lines=1,
        elapsed_seconds=0.1,
    )


def test_policy_bound_evaluation_rejects_merge_before_policy_or_evaluator(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    calls: list[object] = []

    def evaluator(*args: object, **kwargs: object) -> AttemptResult:
        calls.append((args, kwargs))
        return cast(AttemptResult, object())

    with pytest.raises(ValueError, match="cannot merge a live branch"):
        evaluate_policy_bound_managed_proposal(
            tmp_path,
            object(),
            events.append,
            _task(),
            _reference(),
            _bound(tmp_path),
            1,
            evaluator=evaluator,
            expected_attempt_identity_digest="b" * 64,
            merge=True,
        )

    assert calls == []
    assert events == []


def test_policy_bound_evaluation_rechecks_public_scope_around_exactly_one_call(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    runner = object()
    task = _task()
    reference = _reference()
    bound = _bound(tmp_path)
    expected = cast(AttemptResult, object())
    calls: list[tuple[object, ...]] = []

    def evaluator(
        actual_runner: object,
        actual_task: TaskSpec,
        actual_reference: CodexReference,
        actual_bound: PlanBoundProposal,
        attempt: int,
        *,
        expected_attempt_identity_digest: str,
        merge: bool,
    ) -> AttemptResult:
        calls.append(
            (
                actual_runner,
                actual_task,
                actual_reference,
                actual_bound,
                attempt,
                expected_attempt_identity_digest,
                merge,
            )
        )
        return expected

    result = evaluate_policy_bound_managed_proposal(
        tmp_path,
        runner,
        events.append,
        task,
        reference,
        bound,
        2,
        evaluator=evaluator,
        expected_attempt_identity_digest="b" * 64,
        merge=False,
    )

    assert result is expected
    assert calls == [(runner, task, reference, bound, 2, "b" * 64, False)]
    assert len(events) == 1
    assert events[0].startswith("SELF_IMPROVE_POLICY_LOADED ")
    assert "src/public.py" not in events[0]
    assert "tests/unit/test_public.py" not in events[0]


def test_policy_drift_during_evaluation_blocks_result_without_path_disclosure(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    calls = 0

    def evaluator(*_args: object, **_kwargs: object) -> AttemptResult:
        nonlocal calls
        calls += 1
        policy_path = tmp_path / ".gludd" / "self-improve-policy.json"
        policy_path.parent.mkdir(parents=True)
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "default_access": "public",
                    "private_paths": ["src/public.py"],
                    "public_paths": [],
                }
            ),
            encoding="utf-8",
        )
        return cast(AttemptResult, object())

    with pytest.raises(SelfImprovePolicyViolation):
        evaluate_policy_bound_managed_proposal(
            tmp_path,
            object(),
            events.append,
            _task(),
            _reference(),
            _bound(tmp_path),
            1,
            evaluator=evaluator,
            expected_attempt_identity_digest="b" * 64,
            merge=False,
        )

    assert calls == 1
    assert len(events) == 2
    assert events[0].startswith("SELF_IMPROVE_POLICY_LOADED ")
    assert events[1].startswith("SELF_IMPROVE_POLICY_BLOCKED ")
    assert "reason=policy_drift" in events[1]
    assert "src/public.py" not in repr(events)
