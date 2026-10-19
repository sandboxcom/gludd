"""Exact, opt-in context measurements through the existing AB060 owner."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import audit_agent_behavior as audit
import pytest


def test_explicit_context_counts_captured_utf8_bytes_and_physical_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "one.md").write_bytes("é\nsecond".encode())
    (tmp_path / "two.md").write_bytes(b"last\n")
    result = audit.check_ab060_context_size(["one.md", "two.md"])
    assert result["status"] == "PASS"
    assert result["total_bytes"] == 14
    assert result["physical_lines"] == 3
    assert result["total_lines"] == 4
    assert list(result["files"]) == ["one.md", "two.md"]
    assert result["files"]["one.md"]["bytes"] == 9
    assert len(result["files"]["one.md"]["sha256"]) == 64


def test_default_context_retains_legacy_file_set_and_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_text("a\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/CLAUDE.md").write_text("b")
    result = audit.check_ab060_context_size()
    assert list(result["files"]) == ["AGENTS.md", "docs/CLAUDE.md"]
    assert result["total_lines"] == 3
    assert result["physical_lines"] == 2
    assert result["threshold_lines"] == 20000
    assert result["threshold_kb"] == 600


@pytest.mark.parametrize("files", ([], ["missing.md"], ["../outside.md"], ["/tmp/file.md"], ["dir"], ["a.md", "a.md"]))
def test_explicit_context_refuses_incomplete_or_ambiguous_inputs(
    files: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "dir").mkdir()
    result = audit.check_ab060_context_size(files)
    assert result["status"] == "FAIL"
    assert result["violations"]


def test_explicit_context_refuses_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "link.md").symlink_to(tmp_path / "a.md")
    assert audit.check_ab060_context_size(["link.md"])["status"] == "FAIL"


def test_line_threshold_is_not_a_byte_or_preservation_assertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    path = tmp_path / "large.md"
    path.write_text("x" * (601 * 1024))
    assert audit.check_ab060_context_size(["large.md"])["status"] == "PASS"
    path.write_text("\n" * 20000)
    assert audit.check_ab060_context_size(["large.md"])["status"] == "FAIL"


@pytest.mark.parametrize(
    ("raw_files", "raw_json"),
    (("not-json", "1"), ('["AGENTS.md"]', "true"), ('{"file":"AGENTS.md"}', "1"), ("[]", "1")),
)
def test_environment_carriers_fail_closed_before_other_checks(
    raw_files: str, raw_json: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_FILES", raw_files)
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_JSON", raw_json)
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB060", "--context-from-env"])
    with pytest.raises(SystemExit, match="2"):
        audit.main()


def test_context_carrier_refuses_unrelated_audit_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB041", "--context-from-env"])
    with pytest.raises(SystemExit, match="2"):
        audit.main()


def test_context_cli_json_uses_only_requested_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "chosen.md").write_text("chosen")
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_FILES", '["chosen.md"]')
    monkeypatch.setenv("GLUDD_AGENT_CONTEXT_JSON", "1")
    monkeypatch.setattr(sys, "argv", ["audit", "--filter", "AB060", "--context-from-env"])
    assert audit.main() == 0
    result = json.loads(capsys.readouterr().out)["AB060"]
    assert list(result["files"]) == ["chosen.md"]
    assert result["total_bytes"] == 6


@pytest.mark.parametrize("payload", ("$(shell touch INERT-CONTEXT-MARKER)", "\\", "'quoted'"))
def test_real_make_context_value_is_data(
    payload: str, tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    marker = root / "INERT-CONTEXT-MARKER"
    assert not marker.exists()
    result = subprocess.run(
        ["make", "--no-print-directory", "audit-agent-context-size",
         "AGENT_CONTEXT_FILES=" + payload, "AGENT_CONTEXT_JSON=1"],
        cwd=root, env=os.environ.copy(), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "AGENT_CONTEXT_FILES" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("files", (["one.md", "./one.md"], ["./one.md", "one.md"]))
def test_context_alias_pairs_do_not_double_count(
    files: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "one.md").write_text("once")
    result = audit.check_ab060_context_size(files)
    assert result["status"] == "FAIL"
    assert len(result["files"]) == 1
    assert result["total_bytes"] == 4


@pytest.mark.parametrize(("captured", "legacy", "physical"), ((b"a\rb\rc", 3, 1), (b"a\r\nb\r\n", 3, 2)))
def test_context_legacy_count_uses_universal_newlines_from_capture(
    captured: bytes, legacy: int, physical: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "one.md").write_bytes(captured)
    result = audit.check_ab060_context_size(["one.md"])
    assert result["total_lines"] == legacy
    assert result["physical_lines"] == physical
    assert result["total_bytes"] == len(captured)


@pytest.mark.parametrize("separator", (b"\r", b"\r\n"))
def test_default_context_retains_universal_newline_threshold(
    separator: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    (tmp_path / "AGENTS.md").write_bytes(separator * 20000)
    result = audit.check_ab060_context_size()
    assert result["total_lines"] == 20001
    assert result["status"] == "FAIL"


def test_context_refuses_fifo_mode_before_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    candidate = tmp_path / "fifo.md"
    candidate.write_text("fixture")
    original_stat = Path.stat
    original_read = Path.read_bytes

    def fifo_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == candidate:
            return os.stat_result((stat.S_IFIFO, 0, 0, 1, 0, 0, 0, 0, 0, 0))
        return original_stat(path, follow_symlinks=follow_symlinks)

    def forbidden_read(path: Path) -> bytes:
        assert path != candidate, "Nonregular context must be refused before read_bytes"
        return original_read(path)

    monkeypatch.setattr(Path, "stat", fifo_stat)
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    result = audit.check_ab060_context_size(["fifo.md"])
    assert result["status"] == "FAIL"
    assert result["total_bytes"] == 0
