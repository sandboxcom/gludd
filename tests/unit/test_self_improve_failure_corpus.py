"""Offline regression tests for observed local self-improvement failure classes."""

from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

corpus = importlib.import_module("scripts.replay_self_improve_failure_corpus")

ROOT = Path(__file__).resolve().parents[2]
TRACKED_CORPUS = ROOT / "config/self-improve/failure-corpus.json"
EXPECTED_CASES = (
    "eviction-refused-phantom-proposal",
    "no-op-replace",
    "compact-v4-boolean-coordinate",
    "compact-v4-hidden-line",
    "multiline-redundant-metadata",
    "token-exhaustion",
    "worker-success-parent-merge-rejection",
    "raw-native-log-leakage",
    "replace-precondition-mismatch",
    "legacy-outcome-unchanged-identity-empty-plan",
    "compact-v4-hidden-gap-insertion",
    "compact-v4-prefaced-json-object",
)


def _raw_fixture() -> dict[str, object]:
    return cast(
        "dict[str, object]",
        json.loads(TRACKED_CORPUS.read_text(encoding="utf-8")),
    )


def _fixture_cases(value: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", value["cases"])


def _write_fixture(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "failure-corpus.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _canonical_fixture_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def test_tracked_corpus_is_canonical_and_normalization_is_idempotent() -> None:
    raw = TRACKED_CORPUS.read_text(encoding="utf-8")
    payload = json.loads(raw)
    canonical = _canonical_fixture_text(payload)

    assert raw == canonical
    assert canonical == _canonical_fixture_text(json.loads(canonical))


def test_tracked_corpus_replays_every_observed_failure_class() -> None:
    cases = corpus.load_corpus(TRACKED_CORPUS)

    assert tuple(case.case_id for case in cases) == EXPECTED_CASES

    results = corpus.replay_corpus(cases)

    assert tuple(result.case_id for result in results) == EXPECTED_CASES
    assert all(result.passed for result in results)
    assert all(result.feedback_bytes <= 256 for result in results)


@pytest.mark.parametrize(
    ("case_id", "feedback_type", "source", "detail"),
    (
        (
            "compact-v4-boolean-coordinate",
            "edit_span_coordinate",
            "worker_tail",
            "compact span coordinates must be integers, not booleans",
        ),
        (
            "compact-v4-hidden-line",
            "edit_span_scope",
            "parent_validation",
            "compact span must consume only explicitly shown baseline lines",
        ),
        (
            "no-op-replace",
            "edit_replace_contract",
            "worker_tail",
            "replace requires distinct non-empty old_text",
        ),
        (
            "multiline-redundant-metadata",
            "proposal_root_contract",
            "worker_tail",
            "compact proposal must contain exactly e",
        ),
        (
            "token-exhaustion",
            "decode_budget",
            "worker_tail",
            "local model exhausted the proposal token budget before completion",
        ),
        (
            "worker-success-parent-merge-rejection",
            "proposal_scope",
            "worker_tail",
            "proposal shard edits must cover the exact focus paths",
        ),
        (
            "raw-native-log-leakage",
            "edit_replace_contract",
            "proposal_error",
            "replace requires distinct non-empty old_text",
        ),
        (
            "replace-precondition-mismatch",
            "edit_replace_precondition",
            "parent_validation",
            "replace old_text must occur exactly once in trusted baseline",
        ),
        (
            "compact-v4-hidden-gap-insertion",
            "edit_span_scope",
            "parent_validation",
            "compact insertion must use s from the first shown line through one past "
            "the last shown line of one contiguous section",
        ),
        (
            "compact-v4-prefaced-json-object",
            "proposal_json_contract",
            "worker_tail",
            "compact-v4 proposal is not one complete JSON object",
        ),
    ),
)
def test_failure_class_has_exact_typed_feedback(
    case_id: str,
    feedback_type: str,
    source: str,
    detail: str,
) -> None:
    case = next(item for item in corpus.load_corpus(TRACKED_CORPUS) if item.case_id == case_id)

    result = corpus.replay_case(case)

    assert result.feedback_type == feedback_type
    assert result.source == source
    assert result.detail == detail
    assert f"type={feedback_type}" in result.feedback
    assert f"source={source}" in result.feedback
    assert f"detail={detail}" in result.feedback


def test_replace_precondition_mismatch_cites_bounded_captured_evidence() -> None:
    case = next(
        item
        for item in corpus.load_corpus(TRACKED_CORPUS)
        if item.case_id == "replace-precondition-mismatch"
    )

    assert case.kind == "retry_feedback"
    assert case.inputs == {
        "error": (
            "SELF_IMPROVE_PARENT_PROPOSAL_ERROR "
            "replace old_text must occur exactly once in trusted baseline"
        )
    }
    evidence = cast("str", case.inputs["error"])
    assert len(evidence.encode("utf-8")) <= 256

    result = corpus.replay_case(case)
    assert result.feedback_type == "edit_replace_precondition"
    assert result.source == "parent_validation"
    assert result.detail == (
        "replace old_text must occur exactly once in trusted baseline"
    )


def test_parent_rejection_occurs_after_worker_and_batch_decode_succeed() -> None:
    case = next(
        item
        for item in corpus.load_corpus(TRACKED_CORPUS)
        if item.case_id == "worker-success-parent-merge-rejection"
    )

    result = corpus.replay_case(case)

    assert result.worker_succeeded is True
    assert result.parent_stage == "merge"


def test_native_logs_never_escape_typed_feedback_or_cli_output(capsys: pytest.CaptureFixture[str]) -> None:
    case = next(
        item
        for item in corpus.load_corpus(TRACKED_CORPUS)
        if item.case_id == "raw-native-log-leakage"
    )

    result = corpus.replay_case(case)
    exit_code = corpus.main(["--corpus", str(TRACKED_CORPUS)])
    captured = capsys.readouterr()
    published = captured.out + captured.err

    assert exit_code == 0
    for forbidden in case.expected.forbidden_substrings:
        assert forbidden not in result.feedback
        assert forbidden not in published


def test_cli_emits_deterministic_bounded_case_and_summary_evidence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert corpus.main(["--corpus", str(TRACKED_CORPUS)]) == 0

    captured = capsys.readouterr()
    lines = captured.out.splitlines()

    assert len([line for line in lines if line.startswith("SELF_IMPROVE_FAILURE_CORPUS_CASE ")]) == 12
    assert lines[-1] == (
        'SELF_IMPROVE_FAILURE_CORPUS_SUMMARY '
        '{"cases":12,"failed":0,"passed":12,"protocol":"self-improve-failure-corpus-v4"}'
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"extra": True}),
        lambda value: value.pop("protocol"),
        lambda value: value.update({"schema_version": 5}),
        lambda value: value.update({"protocol": "unknown"}),
        lambda value: value.update({"cases": []}),
        lambda value: value["cases"].append(copy.deepcopy(value["cases"][0])),
        lambda value: value["cases"][0].update({"kind": "unknown"}),
        lambda value: value["cases"][0].update({"extra": True}),
        lambda value: value["cases"][0]["expected"].update({"extra": True}),
    ),
)
def test_loader_rejects_schema_drift(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], object],
) -> None:
    value = _raw_fixture()
    mutate(value)

    with pytest.raises(ValueError):
        corpus.load_corpus(_write_fixture(tmp_path, value))


@pytest.mark.parametrize(
    ("case_id", "container_name", "field", "replacement", "match"),
    (
        ("no-op-replace", "expected", "detail", "", "UTF-8 bytes"),
        ("no-op-replace", "expected", "type", "bad type", "not canonical"),
        ("no-op-replace", "expected", "source", "bad source", "not canonical"),
        (
            "no-op-replace",
            "expected",
            "forbidden_substrings",
            "not-a-list",
            "bounded list",
        ),
        (
            "no-op-replace",
            "expected",
            "forbidden_substrings",
            ["duplicate", "duplicate"],
            "unique",
        ),
        ("no-op-replace", "case", "id", "Bad ID", "not canonical"),
        (
            "compact-v4-hidden-line",
            "input",
            "editable_ranges",
            [1],
            "contain pairs",
        ),
        ("token-exhaustion", "input", "budget", True, "positive integer"),
        ("token-exhaustion", "input", "require_stop", 1, "must be boolean"),
        (
            "worker-success-parent-merge-rejection",
            "input",
            "protocol_digest",
            "x" * 64,
            "not canonical",
        ),
    ),
)
def test_loader_rejects_noncanonical_nested_values(
    tmp_path: Path,
    case_id: str,
    container_name: str,
    field: str,
    replacement: object,
    match: str,
) -> None:
    value = _raw_fixture()
    case = next(item for item in _fixture_cases(value) if item["id"] == case_id)
    container = (
        case
        if container_name == "case"
        else cast("dict[str, object]", case[container_name])
    )
    container[field] = replacement

    with pytest.raises(ValueError, match=match):
        corpus.load_corpus(_write_fixture(tmp_path, value))


def test_loader_rejects_non_object_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="failure corpus must be a JSON object"):
        corpus.load_corpus(_write_fixture(tmp_path, []))


def test_loader_rejects_invalid_or_oversized_input(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_bytes(b"\xff")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * 65_537)

    with pytest.raises(ValueError, match="UTF-8 JSON"):
        corpus.load_corpus(invalid)
    with pytest.raises(ValueError, match="65536 bytes"):
        corpus.load_corpus(oversized)
    with pytest.raises(FileNotFoundError):
        corpus.load_corpus(tmp_path / "missing.json")


def test_expectation_drift_fails_closed_without_echoing_model_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    value = _raw_fixture()
    no_op_case = next(
        item for item in _fixture_cases(value) if item["id"] == "no-op-replace"
    )
    expected = cast("dict[str, object]", no_op_case["expected"])
    expected["type"] = "wrong_type"
    path = _write_fixture(tmp_path, value)

    assert corpus.main(["--corpus", str(path)]) == 1

    captured = capsys.readouterr()
    published = captured.out + captured.err
    assert "SELF_IMPROVE_FAILURE_CORPUS_MISMATCH case=no-op-replace" in published
    assert '"e"' not in published
    assert "same model text" not in published


def test_malformed_fixture_error_is_typed_and_does_not_dump_contents(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"secret-looking-but-safe-fixture":"must-not-echo"}', encoding="utf-8")

    assert corpus.main(["--corpus", str(path)]) == 2

    captured = capsys.readouterr()
    assert "SELF_IMPROVE_FAILURE_CORPUS_ERROR type=fixture_validation" in captured.err
    assert "must-not-echo" not in captured.err
