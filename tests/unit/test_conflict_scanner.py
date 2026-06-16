"""Tests for scripts/scan_conflicts.py — the git conflict-marker scanner.

False-positive policy (mirrored from the scanner's docstring):
``<<<<<<<``, ``|||||||`` and ``>>>>>>>`` are never valid prose, so they are
always flagged. A bare ``=======`` is a legitimate markdown horizontal rule /
RST underline, so it is ONLY flagged when the same file also contains a
``<<<<<<<`` or ``>>>>>>>`` marker (a real conflict in progress). Both branches
are exercised below.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
SCRIPT_PATH = ROOT / "scripts" / "scan_conflicts.py"


def _load_module():
    """Import scan_conflicts.py by path, without polluting sys.modules names."""
    spec = importlib.util.spec_from_file_location("scan_conflicts", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("scan_conflicts", module)
    spec.loader.exec_module(module)
    return module


scan_conflicts = _load_module()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_script_exists():
    assert SCRIPT_PATH.is_file(), "scripts/scan_conflicts.py must exist"


def test_clean_file_passes(tmp_path: Path):
    p = _write(tmp_path, "clean.py", "x = 1\ny = 2\nprint(x + y)\n")
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_clean_file_with_long_equals_run_is_not_a_marker(tmp_path: Path):
    # A run of equals longer/different than the 7-char marker in normal code.
    p = _write(tmp_path, "ok.py", "if a == b == c:\n    pass\n")
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_ours_marker_flagged_with_line(tmp_path: Path):
    body = "line1\n<<<<<<< HEAD\nline3\n"
    p = _write(tmp_path, "ours.txt", body)
    findings = scan_conflicts.scan_paths([str(p)])
    assert findings == [(str(p), 2, "<<<<<<<")]


def test_theirs_marker_flagged_with_line(tmp_path: Path):
    body = "a\nb\n>>>>>>> feature/x\n"
    p = _write(tmp_path, "theirs.txt", body)
    findings = scan_conflicts.scan_paths([str(p)])
    assert findings == [(str(p), 3, ">>>>>>>")]


def test_merge_base_marker_flagged_with_line(tmp_path: Path):
    body = "a\n||||||| merged common ancestors\nb\n"
    p = _write(tmp_path, "base.txt", body)
    findings = scan_conflicts.scan_paths([str(p)])
    assert findings == [(str(p), 2, "|||||||")]


def test_full_conflict_block_flags_all_four_markers(tmp_path: Path):
    body = (
        "intro\n"
        "<<<<<<< HEAD\n"
        "ours\n"
        "||||||| base\n"
        "common\n"
        "=======\n"
        "theirs\n"
        ">>>>>>> branch\n"
    )
    p = _write(tmp_path, "conflict.txt", body)
    findings = scan_conflicts.scan_paths([str(p)])
    markers = [m for (_, _, m) in findings]
    lines = [ln for (_, ln, _) in findings]
    assert markers == ["<<<<<<<", "|||||||", "=======", ">>>>>>>"]
    assert lines == [2, 4, 6, 8]


def test_bare_equals_separator_not_flagged_in_markdown_rule(tmp_path: Path):
    # Markdown setext heading underline / horizontal rule uses ====... lines.
    body = "My Title\n=======\n\nSome body text.\n"
    p = _write(tmp_path, "doc.md", body)
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_bare_equals_separator_not_flagged_in_rst_underline(tmp_path: Path):
    body = "Section\n=======\n\nProse.\n"
    p = _write(tmp_path, "doc.rst", body)
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_equals_flagged_only_when_real_conflict_present(tmp_path: Path):
    # Same ======= line, but now there's a <<<<<<< above it -> real conflict.
    body = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n"
    p = _write(tmp_path, "real.txt", body)
    findings = scan_conflicts.scan_paths([str(p)])
    markers = sorted({m for (_, _, m) in findings})
    assert markers == ["<<<<<<<", "=======", ">>>>>>>"]
    # The separator is among the flagged lines.
    assert any(m == "=======" for (_, _, m) in findings)


def test_binary_file_skipped(tmp_path: Path):
    # A NUL byte marks binary; even if it "contains" marker-looking bytes, skip.
    p = tmp_path / "blob.bin"
    p.write_bytes(b"<<<<<<< HEAD\x00\x00garbage")
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_fixture_dir_is_skipped(tmp_path: Path):
    fixtures = tmp_path / "conflict_fixtures"
    fixtures.mkdir()
    p = fixtures / "embed.txt"
    p.write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n", encoding="utf-8")
    assert scan_conflicts.scan_paths([str(p)]) == []


def test_nonexistent_path_skipped(tmp_path: Path):
    assert scan_conflicts.scan_paths([str(tmp_path / "nope.txt")]) == []


def test_main_returns_zero_for_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    p = _write(tmp_path, "clean.py", "x = 1\n")
    rc = scan_conflicts.main([str(p)])
    assert rc == 0


def test_main_returns_nonzero_and_prints_for_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    p = _write(tmp_path, "bad.txt", "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> x\n")
    rc = scan_conflicts.main([str(p)])
    assert rc == 1
    out = capsys.readouterr()
    assert str(p) in out.out
    assert "conflict marker" in out.out


def test_multiple_files_sorted(tmp_path: Path):
    a = _write(tmp_path, "a.txt", "<<<<<<< HEAD\n")
    b = _write(tmp_path, "b.txt", ">>>>>>> x\n")
    findings = scan_conflicts.scan_paths([str(b), str(a)])
    # Sorted by path so a.txt comes before b.txt regardless of argv order.
    assert findings[0][0] == str(a)
    assert findings[1][0] == str(b)
