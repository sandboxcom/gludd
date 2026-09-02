"""Offline regression tests for observed local self-improvement failure classes."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import scripts.replay_self_improve_failure_corpus as corpus

ROOT = Path(__file__).resolve().parents[2]
TRACKED_CORPUS = ROOT / "config/self-improve/failure-corpus.json"
EXPECTED_CASES = (
    "eviction-refused-phantom-proposal",
    "no-op-replace",
    "multiline-redundant-metadata",
    "token-exhaustion",
    "worker-success-parent-merge-rejection",
    "raw-native-log-leakage",
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

    assert len([line for line in lines if line.startswith("SELF_IMPROVE_FAILURE_CORPUS_CASE ")]) == 6
    assert lines[-1] == (
        'SELF_IMPROVE_FAILURE_CORPUS_SUMMARY '
        '{"cases":6,"failed":0,"passed":6,"protocol":"self-improve-failure-corpus-v2"}'
    )
    assert captured.err == ""


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update({"extra": True}),
        lambda value: value.pop("protocol"),
        lambda value: value.update({"schema_version": 3}),
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
