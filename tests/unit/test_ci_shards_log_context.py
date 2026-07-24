from __future__ import annotations

from pathlib import Path

import pytest
from scripts.ci_shards_log_context import extract_context


def test_extract_context_shows_matching_line_with_neighbors(tmp_path: Path) -> None:
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_file = log_dir / "ci.log"
    log_file.write_text(
        "\n".join(
            [
                "alpha",
                "FAILED tests/unit/test_feature_repo.py::test_a",
                "trace line",
                "omega",
            ]
        ),
        encoding="utf-8",
    )

    output = extract_context(
        log_file,
        "FAILED tests/unit/test_feature_repo.py",
        before=1,
        after=1,
    )

    assert ">2: FAILED tests/unit/test_feature_repo.py::test_a" in output
    assert " 1: alpha" in output
    assert " 3: trace line" in output


def test_extract_context_rejects_non_gate_log(tmp_path: Path) -> None:
    outside = tmp_path / "ci.log"
    outside.write_text("FAILED tests/unit/test_feature_repo.py", encoding="utf-8")

    with pytest.raises(ValueError, match="outside workspace gate logs"):
        extract_context(outside, "FAILED")
