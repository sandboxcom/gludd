"""Strict durable artifact contracts for managed self-improvement results."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

import general_ludd.self_improve as self_improve_package
import general_ludd.self_improve.result_artifact as artifact_module
from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import AttemptResult, ManagedRunResult
from general_ludd.self_improve.result_artifact import (
    ManagedSelfImproveResultArtifact,
)


def _proposal() -> ProposalManifest:
    return ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": 1,
                "baseline_sha": "a" * 40,
                "task_id": "S83.203",
                "edits": [
                    {
                        "operation": "replace",
                        "path": "src/general_ludd/example.py",
                        "old_text": "return 0",
                        "new_text": "return 1",
                    }
                ],
                "tests": ["tests/unit/test_example.py"],
                "make_commands": [
                    "make test-files TESTFILES=tests/unit/test_example.py"
                ],
                "commit_message": "feat: improve example",
            }
        )
    )


def _run_result(*, accepted: bool = True) -> ManagedRunResult:
    proposal = _proposal()
    comparison = ComparisonResult(
        accepted=accepted,
        score=100.0 if accepted else 45.0,
        blockers=() if accepted else ("tests",),
        changed_file_precision=1.0,
        changed_file_recall=1.0,
    )
    evidence = CandidateEvidence(
        changed_files=frozenset({"src/general_ludd/example.py"}),
        tests_passed=accepted,
        warnings=0,
        coverage_aggregate=91.5,
        coverage_min_file=82.0,
        ruff_passed=True,
        mypy_passed=True,
        docstrings_passed=True,
        markdown_passed=True,
        cleanup_passed=True,
        commit_count=1,
        worktree_clean=True,
        elapsed_seconds=0.25,
        changed_lines=2,
    )
    return ManagedRunResult(
        final_result=AttemptResult(
            comparison=comparison,
            evidence=evidence,
            patch_equivalence="e" * 64,
            proposal=proposal,
            diagnostics="" if accepted else "tests failed",
            attempt_identity_digest="d" * 64,
        ),
        attempts=1,
        plan_identity_digest="c" * 64,
        attempted_model_ids=("model-one",),
        outcome_record_ids=("17",),
    )


def _artifact_value() -> dict[str, Any]:
    value = json.loads(
        ManagedSelfImproveResultArtifact.from_run_result(_run_result()).to_json()
    )
    assert isinstance(value, dict)
    return value


def test_result_artifact_round_trip_is_canonical_and_reviewable() -> None:
    artifact = ManagedSelfImproveResultArtifact.from_run_result(_run_result())

    encoded = artifact.to_json()
    restored = ManagedSelfImproveResultArtifact.from_json(encoded)

    assert restored == artifact
    assert restored.to_json() == encoded
    assert restored.accepted is True
    assert restored.proposal.edits[0].new_text == "return 1"
    assert restored.evidence.tests_passed is True
    assert restored.comparison.score == 100.0
    assert restored.artifact_digest == json.loads(encoded)["artifact_digest"]


def test_result_artifact_preserves_rejected_evidence_and_diagnostics() -> None:
    artifact = ManagedSelfImproveResultArtifact.from_run_result(
        _run_result(accepted=False)
    )

    restored = ManagedSelfImproveResultArtifact.from_json(artifact.to_json())

    assert restored.accepted is False
    assert restored.comparison.blockers == ("tests",)
    assert restored.diagnostics == "tests failed"


def test_result_artifact_rejects_payload_tampering() -> None:
    value = _artifact_value()
    value["evidence"]["tests_passed"] = False

    with pytest.raises(ValueError, match="artifact digest"):
        ManagedSelfImproveResultArtifact.from_json(json.dumps(value))


def test_result_artifact_rejects_unknown_fields() -> None:
    value = _artifact_value()
    value["unexpected"] = True

    with pytest.raises(ValueError, match="unknown fields"):
        ManagedSelfImproveResultArtifact.from_json(json.dumps(value))


@pytest.mark.parametrize(
    "mutated",
    [
        lambda result: replace(result, attempts=0),
        lambda result: replace(result, plan_identity_digest="not-a-digest"),
        lambda result: replace(
            result,
            final_result=replace(
                result.final_result,
                diagnostics="x" * 65_537,
            ),
        ),
        lambda result: replace(
            result,
            final_result=replace(
                result.final_result,
                evidence=replace(
                    result.final_result.evidence,
                    changed_files=frozenset({"../outside.py"}),
                ),
            ),
        ),
    ],
)
def test_result_artifact_rejects_invalid_or_unbounded_run_results(
    mutated: Callable[[ManagedRunResult], ManagedRunResult],
) -> None:
    with pytest.raises(ValueError):
        ManagedSelfImproveResultArtifact.from_run_result(mutated(_run_result()))


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("accepted",), "yes"),
        (("attempts",), True),
        (("attempts",), 33),
        (("attempted_model_ids",), "model-one"),
        (("attempted_model_ids",), ["model-one", "model-two"]),
        (("attempted_model_ids",), ["model-one", "model-one"]),
        (("attempted_model_ids",), [None]),
        (("attempted_model_ids",), [""]),
        (("attempted_model_ids",), ["bad\x00id"]),
        (("patch_equivalence",), ""),
        (("plan_identity_digest",), "not-a-digest"),
        (("comparison", "score"), "high"),
        (("comparison", "score"), float("nan")),
        (("comparison", "score"), 101.0),
        (("comparison", "blockers"), "tests"),
        (("comparison", "blockers"), ["tests", "tests"]),
        (("evidence", "changed_files"), "src/example.py"),
        (("evidence", "changed_files"), []),
        (
            ("evidence", "changed_files"),
            ["src/general_ludd/example.py", "src/general_ludd/example.py"],
        ),
        (("evidence", "changed_files"), ["src/general_ludd/other.py"]),
        (("evidence", "tests_passed"), 1),
    ],
)
def test_result_artifact_rejects_malformed_nested_fields(
    path: tuple[str, ...],
    invalid_value: object,
) -> None:
    value = _artifact_value()
    cursor = value
    for key in path[:-1]:
        nested = cursor[key]
        assert isinstance(nested, dict)
        cursor = nested
    cursor[path[-1]] = invalid_value

    with pytest.raises(ValueError):
        ManagedSelfImproveResultArtifact.from_json(json.dumps(value))


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        "[]",
        17,
    ],
)
def test_result_artifact_rejects_invalid_json_roots(raw: object) -> None:
    with pytest.raises(ValueError):
        ManagedSelfImproveResultArtifact.from_json(raw)  # type: ignore[arg-type]


def test_result_artifact_rejects_missing_fields() -> None:
    value = _artifact_value()
    del value["proposal"]

    with pytest.raises(ValueError, match="missing fields"):
        ManagedSelfImproveResultArtifact.from_json(json.dumps(value))


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("kind", "other", "kind"),
        ("schema_version", 2, "schema_version"),
        ("schema_version", True, "schema_version"),
        ("schema_version", 1.0, "schema_version"),
    ],
)
def test_result_artifact_rejects_wrong_envelope_identity(
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    value = _artifact_value()
    value[field] = invalid_value

    with pytest.raises(ValueError, match=message):
        ManagedSelfImproveResultArtifact.from_json(json.dumps(value))


def test_result_artifact_rejects_duplicate_json_keys() -> None:
    encoded = ManagedSelfImproveResultArtifact.from_run_result(_run_result()).to_json()
    duplicated = encoded.replace(
        '"accepted":true',
        '"accepted":true,"accepted":true',
        1,
    )

    with pytest.raises(ValueError, match="duplicate field"):
        ManagedSelfImproveResultArtifact.from_json(duplicated)


def test_result_artifact_rejects_oversize_json() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        ManagedSelfImproveResultArtifact.from_json("x" * 5_242_881)


def test_result_artifact_rechecks_size_when_serializing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = ManagedSelfImproveResultArtifact.from_run_result(_run_result())
    monkeypatch.setattr(artifact_module, "_MAX_ARTIFACT_BYTES", 1)

    with pytest.raises(ValueError, match="exceeds"):
        artifact.to_json()


def test_result_artifact_rejects_wrong_runtime_types() -> None:
    with pytest.raises(ValueError, match="ManagedRunResult"):
        ManagedSelfImproveResultArtifact.from_run_result(
            cast(ManagedRunResult, SimpleNamespace())
        )

    wrong_final = replace(
        _run_result(),
        final_result=cast(AttemptResult, SimpleNamespace()),
    )
    with pytest.raises(ValueError, match="AttemptResult"):
        ManagedSelfImproveResultArtifact.from_run_result(wrong_final)


@pytest.mark.parametrize(
    "field",
    ["proposal", "evidence", "comparison"],
)
def test_result_artifact_rejects_wrong_final_result_members(field: str) -> None:
    result = _run_result()
    invalid_final = cast(
        AttemptResult,
        replace(cast(Any, result.final_result), **{field: None}),
    )
    invalid_result = replace(result, final_result=invalid_final)

    with pytest.raises(ValueError):
        ManagedSelfImproveResultArtifact.from_run_result(invalid_result)


def test_result_artifact_is_exported_from_installed_package() -> None:
    assert (
        self_improve_package.ManagedSelfImproveResultArtifact
        is ManagedSelfImproveResultArtifact
    )
