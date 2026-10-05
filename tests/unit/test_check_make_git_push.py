"""Tests for executable-only Makefile git-push policy scanning."""

import subprocess
import sys
from pathlib import Path

import pytest
from scripts import check_make_git_push as checker

ROOT = Path(__file__).resolve().parent.parent.parent


def test_repository_guard_ignores_explanatory_text() -> None:
    """The real release guard scans commands, not comments or diagnostics."""
    completed = subprocess.run(
        ["make", "--no-print-directory", "_no-raw-git-guard"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "_no-raw-git-guard: PASS" in completed.stdout


def test_ignores_comments_and_quoted_diagnostics() -> None:
    makefile = """# raw git push is forbidden
_guard:
\t@grep -n 'git push' Makefile
\t@echo "raw git push detected"
"""
    assert checker.find_unprefixed_pushes(makefile) == []


def test_rejects_real_unprefixed_push_recipe() -> None:
    makefile = """publish:
\t@git push sandboxcom development
"""
    assert checker.find_unprefixed_pushes(makefile) == [
        "2: git push sandboxcom development"
    ]


def test_accepts_ssh_prefixed_push_recipe() -> None:
    makefile = """publish:
\t@GIT_SSH_COMMAND='ssh -i key' git push sandboxcom development
"""
    assert checker.find_unprefixed_pushes(makefile) == []


def test_checks_each_shell_segment_independently() -> None:
    makefile = """publish:
\t@GIT_SSH_COMMAND='ssh -i key' echo ready; git push sandboxcom development
"""
    assert checker.find_unprefixed_pushes(makefile) == [
        "2: GIT_SSH_COMMAND='ssh -i key' echo ready; git push sandboxcom development"
    ]


def test_combines_continued_recipe_before_policy_check() -> None:
    makefile = """publish:
\t@GIT_SSH_COMMAND='ssh -i key' \\\n\tgit push sandboxcom development
"""
    assert checker.find_unprefixed_pushes(makefile) == []


def test_unterminated_recipe_fails_closed() -> None:
    makefile = """publish:
\t@git push sandboxcom 'unterminated
"""
    assert checker.find_unprefixed_pushes(makefile) == [
        "2: git push sandboxcom 'unterminated"
    ]


def test_dangling_continuation_is_still_checked() -> None:
    makefile = "publish:\n\t@git push sandboxcom development \\\n"
    assert checker.find_unprefixed_pushes(makefile) == [
        "2: git push sandboxcom development"
    ]


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
) -> int:
    monkeypatch.setattr(sys, "argv", ["check_make_git_push.py", str(path)])
    return checker.main()


def test_main_reports_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "Makefile"
    path.write_text("publish:\n\t@GIT_SSH_COMMAND=x git push origin main\n")
    assert _run_main(monkeypatch, path) == 0
    assert "CHECK_MAKE_GIT_PUSH_PASS findings=0" in capsys.readouterr().out


def test_main_reports_findings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "Makefile"
    path.write_text("publish:\n\t@git push origin main\n")
    assert _run_main(monkeypatch, path) == 1
    output = capsys.readouterr().out
    assert "CHECK_MAKE_GIT_PUSH_VIOLATION 2: git push origin main" in output
    assert "CHECK_MAKE_GIT_PUSH_FAIL findings=1" in output


@pytest.mark.parametrize("invalid_kind", ["missing", "non_utf8"])
def test_main_reports_bounded_read_failure(
    invalid_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "Makefile"
    if invalid_kind == "non_utf8":
        path.write_bytes(b"\xff")
    assert _run_main(monkeypatch, path) == 2
    assert "CHECK_MAKE_GIT_PUSH_FAIL" in capsys.readouterr().out
