"""Require bounded Unicode-safe display without changing complete CI evidence."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from scripts import ci_shards_log_context as context


def _log_file(tmp_path: Path, content: str) -> Path:
    log_root = tmp_path / ".gate-logs"
    log_root.mkdir()
    log_file = log_root / "ci.log"
    log_file.write_text(content, encoding="utf-8")
    return log_file


def _printed_bytes(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_context_byte_limits_are_fixed_and_include_printed_newlines() -> None:
    assert context._MAX_LINE_BYTES == 2_048
    assert context._MAX_OUTPUT_BYTES == 32_768


def test_normal_overlapping_context_keeps_literal_matching_and_exact_output(
    tmp_path: Path,
) -> None:
    log_file = _log_file(
        tmp_path,
        "alpha\nFAILED [x].*\nFAILED x-other\nFAILED [x].* second\nomega\n",
    )

    output = context.extract_context(log_file, "[x].*", before=1, after=2)

    assert output == [
        "--- match 1/2 line 2 ---",
        " 1: alpha",
        ">2: FAILED [x].*",
        " 3: FAILED x-other",
        " 4: FAILED [x].* second",
        "--- match 2/2 line 4 ---",
        " 3: FAILED x-other",
        ">4: FAILED [x].* second",
        " 5: omega",
    ]
    assert log_file.read_text(encoding="utf-8") == (
        "alpha\nFAILED [x].*\nFAILED x-other\nFAILED [x].* second\nomega\n"
    )


@pytest.mark.parametrize("character", ["x", "🐍", "é", "界"])
def test_long_matching_and_nonmatching_lines_are_bounded_without_corrupting_unicode(
    tmp_path: Path,
    character: str,
) -> None:
    pattern = "FAILED [x].*"
    source = (
        f"{character * 60_001}\n"
        f"{character * 60_001}{pattern}{character * 60_001}\n"
    )
    log_file = _log_file(tmp_path, source)

    output = context.extract_context(log_file, pattern, before=1, after=0)
    printed = _printed_bytes(output)

    assert output[0] == "--- match 1/1 line 2 ---"
    assert output[1].startswith(f" 1: {character}")
    assert "line truncated" in output[1]
    assert output[2].startswith(">2: [line truncated before match] ")
    assert pattern in output[2]
    assert "line truncated" in output[2]
    assert "�" not in printed.decode("utf-8", errors="strict")
    assert all(len(line.encode("utf-8")) + 1 <= 2_048 for line in output)
    assert len(printed) <= 32_768
    assert log_file.read_text(encoding="utf-8") == source


@pytest.mark.parametrize("extra_byte", [0, 1])
def test_line_byte_boundary_counts_the_source_prefix_and_newline(
    tmp_path: Path,
    extra_byte: int,
) -> None:
    pattern = "FAILED"
    prefix = ">1: "
    padding_bytes = 2_048 - 1 - len(prefix.encode("utf-8")) - len(pattern)
    source_line = pattern + "x" * (padding_bytes + extra_byte)
    log_file = _log_file(tmp_path, source_line + "\n")

    output = context.extract_context(log_file, pattern, before=0, after=0)

    if extra_byte:
        assert "line truncated" in output[1]
        assert len(output[1].encode("utf-8")) + 1 <= 2_048
    else:
        assert output[1] == prefix + source_line
        assert len(output[1].encode("utf-8")) + 1 == 2_048


def test_long_matching_line_keeps_its_head_when_the_literal_hit_fits(
    tmp_path: Path,
) -> None:
    source_line = "trace FAILED " + "🐍" * 60_000
    log_file = _log_file(tmp_path, source_line + "\n")

    output = context.extract_context(log_file, "FAILED", before=0, after=0)

    assert output[1].startswith(">1: trace FAILED ")
    assert "line truncated before match" not in output[1]
    assert "line truncated; source unmodified" in output[1]
    assert len(output[1].encode("utf-8")) + 1 <= 2_048


def test_late_literal_hit_is_located_by_utf8_bytes_instead_of_character_count(
    tmp_path: Path,
) -> None:
    log_file = _log_file(tmp_path, "🐍" * 600 + "FAILED finished\n")

    output = context.extract_context(log_file, "FAILED", before=0, after=0)

    assert output[1].startswith(">1: [line truncated before match] FAILED finished")
    assert "line truncated; source unmodified" in output[1]
    assert len(output[1].encode("utf-8")) + 1 <= 2_048


def test_cumulative_bound_preserves_source_order_and_declares_omitted_context(
    tmp_path: Path,
) -> None:
    source = "\n".join(
        "FAILED " + str(index) + " " + "🐍" * 200 for index in range(100)
    )
    log_file = _log_file(tmp_path, source)

    output = context.extract_context(
        log_file, "FAILED", before=0, after=0, max_matches=100
    )

    assert output[0] == "--- match 1/100 line 1 ---"
    assert output[-1] == (
        "--- output truncated: 32768-byte UTF-8 limit; source unmodified ---"
    )
    assert len(_printed_bytes(output)) <= 32_768
    assert all(len(line.encode("utf-8")) + 1 <= 2_048 for line in output)
    source_numbers = [
        int(line[1:].split(":", 1)[0]) for line in output if line[0] == ">"
    ]
    assert source_numbers == list(range(1, len(source_numbers) + 1))
    assert len(source_numbers) < 100
    assert log_file.read_text(encoding="utf-8") == source


def test_total_boundary_preserves_fitting_output_then_makes_room_for_notice(
    tmp_path: Path,
) -> None:
    # With one header and 20 source prefixes/newlines, 32,631 content bytes
    # produce exactly 32,768 printed UTF-8 bytes. Each line remains under 2,048.
    source_lines = ["FAILED " + "x" * 1_623] * 19 + ["FAILED " + "x" * 1_654]
    log_file = _log_file(tmp_path, "\n".join(source_lines))
    original = context.extract_context(
        log_file, "FAILED", before=0, after=19, max_matches=1
    )

    assert len(_printed_bytes(original)) == 32_768
    assert len(original) == 21
    assert not any("truncated" in line for line in original)

    log_file.write_text("\n".join([*source_lines, "tail"]), encoding="utf-8")
    output = context.extract_context(
        log_file, "FAILED", before=0, after=20, max_matches=1
    )

    assert len(_printed_bytes(output)) <= 32_768
    assert output[:-1] == original[:-1]
    assert output[-1].startswith("--- output truncated:")
    assert "tail" not in output[-1]


def test_no_match_diagnostic_also_bounds_an_oversized_unicode_pattern(
    tmp_path: Path,
) -> None:
    log_file = _log_file(tmp_path, "all green\n")

    output = context.extract_context(log_file, "🐍" * 60_000)

    assert len(output) == 1
    assert output[0].startswith("No matches for pattern: ")
    assert "line truncated; source unmodified" in output[0]
    assert "�" not in output[0]
    assert len(_printed_bytes(output)) <= 2_048


def test_oversized_literal_match_remains_a_match_when_the_pattern_cannot_fit(
    tmp_path: Path,
) -> None:
    pattern = "🐍" * 60_000
    log_file = _log_file(tmp_path, pattern + "\n")

    output = context.extract_context(log_file, pattern, before=0, after=0)

    assert output[0] == "--- match 1/1 line 1 ---"
    assert output[1].startswith(">1: 🐍")
    assert "line truncated before match" not in output[1]
    assert "line truncated; source unmodified" in output[1]
    assert len(output[1].encode("utf-8")) + 1 <= 2_048


def test_artifact_cli_keeps_success_status_and_complete_exact_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exact_root = tmp_path / "run-412345" / "failure-diag-unit-8"
    exact_root.mkdir(parents=True)
    exact_log = exact_root / "failure.log"
    source = "FAILED " + "🐍" * 60_000 + "\n"
    exact_log.write_text(source, encoding="utf-8")
    other_root = tmp_path / "run-412344" / "failure-diag-unit-8"
    other_root.mkdir(parents=True)
    (other_root / "failure.log").write_text("FAILED wrong run\n", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_shards_log_context.py",
            "--artifact-root",
            str(exact_root),
            "--artifact-file",
            "failure.log",
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

    assert context.main() == 0
    printed = capsys.readouterr().out
    assert ">1: FAILED" in printed
    assert "line truncated; source unmodified" in printed
    assert "wrong run" not in printed
    assert len(printed.encode("utf-8")) <= 32_768
    assert exact_log.read_text(encoding="utf-8") == source


def test_cli_error_keeps_failure_status_with_a_bounded_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ci_shards_log_context.py",
            "--artifact-root",
            str(artifact_root),
            "--artifact-file",
            "unsafe/" + "🐍" * 60_000,
            "--pattern",
            "FAILED",
        ],
    )

    assert context.main() == 1
    printed = capsys.readouterr().out
    assert printed.startswith(
        "CI-SHARDS-LOG-CONTEXT error: artifact file must be a safe basename:"
    )
    assert "line truncated; source unmodified" in printed
    assert len(printed.encode("utf-8")) <= 2_048
