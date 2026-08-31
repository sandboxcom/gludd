from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts.ci_shards_log_context import (
    extract_context,
    main,
    resolve_artifact_file,
)


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



def test_resolve_artifact_file_is_exact_and_confined(tmp_path: Path) -> None:
    artifact_root = tmp_path / "run-1" / "failure-diag"
    nested = artifact_root / "home" / "runner"
    nested.mkdir(parents=True)
    log_file = nested / "failure.log"
    log_file.write_text("Timeout", encoding="utf-8")

    assert resolve_artifact_file(artifact_root, "failure.log") == log_file.resolve()


def test_resolve_artifact_file_rejects_duplicates_and_unsafe_names(
    tmp_path: Path,
) -> None:
    artifact_root = tmp_path / "artifact"
    first = artifact_root / "one"
    second = artifact_root / "two"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "failure.log").write_text("first", encoding="utf-8")
    (second / "failure.log").write_text("second", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one"):
        resolve_artifact_file(artifact_root, "failure.log")
    with pytest.raises(ValueError, match="safe basename"):
        resolve_artifact_file(artifact_root, "../failure.log")


def test_resolve_artifact_file_rejects_symlink_escape(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    outside = tmp_path / "failure.log"
    outside.write_text("Timeout", encoding="utf-8")
    (artifact_root / "failure.log").symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        resolve_artifact_file(artifact_root, "failure.log")


def test_extract_context_accepts_an_explicit_artifact_root(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    log_file = artifact_root / "failure.log"
    log_file.write_text("alpha\nTimeout (>180.0s)\nomega\n", encoding="utf-8")

    output = extract_context(
        log_file,
        "Timeout",
        before=1,
        after=1,
        allowed_root=artifact_root,
    )

    assert ">2: Timeout (>180.0s)" in output



def test_extract_context_reports_no_matches(tmp_path: Path) -> None:
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_file = log_dir / "ci.log"
    log_file.write_text("all green\n", encoding="utf-8")

    assert extract_context(log_file, "FAILED") == ["No matches for pattern: FAILED"]


def test_artifact_context_rejects_missing_roots_and_invalid_bounds(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        resolve_artifact_file(missing_root, "failure.log")

    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    directory_match = artifact_root / "failure.log"
    directory_match.mkdir()
    with pytest.raises(ValueError, match="exactly one"):
        resolve_artifact_file(artifact_root, "failure.log")

    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_file = log_dir / "ci.log"
    log_file.write_text("FAILED\n", encoding="utf-8")
    with pytest.raises(ValueError, match="pattern is required"):
        extract_context(log_file, "")
    with pytest.raises(ValueError, match="before/after"):
        extract_context(log_file, "FAILED", before=-1)
    with pytest.raises(ValueError, match="outside artifact root"):
        extract_context(log_file, "FAILED", allowed_root=artifact_root)


def test_resolve_artifact_file_rejects_a_symlink_root(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    symlink_root = tmp_path / "artifact"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ValueError, match="root must not be a symlink"):
        resolve_artifact_file(symlink_root, "failure.log")


def test_main_reads_local_and_artifact_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    local_log = log_dir / "ci.log"
    local_log.write_text("alpha\nFAILED\nomega\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_shards_log_context.py",
            "--log",
            str(local_log),
            "--pattern",
            "FAILED",
            "--before",
            "0",
            "--after",
            "0",
            "--max-matches",
            "1",
        ],
    )
    assert main() == 0
    assert ">2: FAILED" in capsys.readouterr().out

    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    artifact_log = artifact_root / "failure.log"
    artifact_log.write_text("Timeout\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_shards_log_context.py",
            "--artifact-root",
            str(artifact_root),
            "--artifact-file",
            "failure.log",
            "--pattern",
            "Timeout",
        ],
    )
    assert main() == 0
    assert ">1: Timeout" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--artifact-root", "ROOT", "--pattern", "FAILED"], "--artifact-file"),
        (
            ["--log", "LOG", "--artifact-file", "failure.log", "--pattern", "FAILED"],
            "--artifact-file requires",
        ),
    ],
)
def test_main_fails_closed_for_incomplete_source_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    message: str,
) -> None:
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    log_dir = tmp_path / ".gate-logs"
    log_dir.mkdir()
    log_file = log_dir / "ci.log"
    log_file.write_text("FAILED\n", encoding="utf-8")
    resolved = [
        str(artifact_root) if value == "ROOT" else str(log_file) if value == "LOG" else value
        for value in arguments
    ]
    monkeypatch.setattr(sys, "argv", ["ci_shards_log_context.py", *resolved])

    assert main() == 1
    assert message in capsys.readouterr().out
