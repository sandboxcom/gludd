"""Fail-closed edge coverage for the offline self-improvement corpus."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
import scripts.replay_self_improve_failure_corpus as corpus

ROOT = Path(__file__).resolve().parents[2]
TRACKED_CORPUS = ROOT / "config/self-improve/failure-corpus.json"


def _root() -> dict[str, object]:
    return cast(
        "dict[str, object]",
        json.loads(TRACKED_CORPUS.read_text(encoding="utf-8")),
    )


def _cases(root: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", root["cases"])


def _object(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value)


def _write(tmp_path: Path, value: object) -> Path:
    path = tmp_path / "edge.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("case_index", "section", "field", "replacement"),
    (
        (0, "case", "id", "Bad_ID"),
        (0, "input", "focus_path", ""),
        (0, "input", "model_output", 42),
        (0, "expected", "type", "Bad-Type"),
        (0, "expected", "source", "Bad-Source"),
        (0, "expected", "forbidden_substrings", "not-a-list"),
        (0, "expected", "forbidden_substrings", ["duplicate", "duplicate"]),
        (2, "input", "budget", True),
        (2, "input", "budget", 0),
        (2, "input", "require_stop", "true"),
        (2, "input", "worker_response", []),
        (3, "input", "protocol_digest", "a" * 63),
        (4, "input", "error", ""),
    ),
)
def test_loader_rejects_field_level_contract_drift(
    tmp_path: Path,
    case_index: int,
    section: str,
    field: str,
    replacement: object,
) -> None:
    root = _root()
    case = _cases(root)[case_index]
    target = case if section == "case" else _object(case[section])
    target[field] = copy.deepcopy(replacement)

    with pytest.raises(ValueError):
        corpus.load_corpus(_write(tmp_path, root))


@pytest.mark.parametrize("root_value", ([], "not-an-object", 7))
def test_loader_rejects_non_object_roots(
    tmp_path: Path,
    root_value: object,
) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        corpus.load_corpus(_write(tmp_path, root_value))


def test_valid_compact_output_is_not_misclassified_as_a_failure() -> None:
    tracked = corpus.load_corpus(TRACKED_CORPUS)[0]
    valid = corpus.FailureCase(
        case_id="valid-output",
        kind="compact_decode",
        inputs={
            "focus_path": "src/general_ludd/self_improve/example.py",
            "model_output": '{"e":[{"a":"old","z":"new"}]}',
        },
        expected=tracked.expected,
    )

    with pytest.raises(corpus.CorpusMismatch) as raised:
        corpus.replay_case(valid)

    assert raised.value.reason == "expected_rejection_missing"


def test_matching_parent_scope_is_not_misclassified_as_a_rejection() -> None:
    tracked = corpus.load_corpus(TRACKED_CORPUS)[3]
    matching = corpus.FailureCase(
        case_id="matching-parent-scope",
        kind="parent_merge",
        inputs={
            "worker_path": "src/general_ludd/self_improve/same.py",
            "expected_path": "src/general_ludd/self_improve/same.py",
            "protocol_digest": "b" * 64,
        },
        expected=tracked.expected,
    )

    with pytest.raises(corpus.CorpusMismatch) as raised:
        corpus.replay_case(matching)

    assert raised.value.reason == "expected_parent_rejection_missing"
