from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from subprocess import CompletedProcess

from scripts.check_clean_tree import check_clean_tree, dirty_lines


def _cp(stdout: str = "", stderr: str = "", returncode: int = 0) -> CompletedProcess[str]:
    return CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_dirty_lines_ignores_blank_lines() -> None:
    assert dirty_lines(" M file.py" + chr(10) + chr(10) + "?? new.py" + chr(10)) == [" M file.py", "?? new.py"]


def test_check_clean_tree_passes_when_porcelain_empty(capsys) -> None:
    def run(argv: Sequence[str]) -> CompletedProcess[str]:
        assert list(argv) == ["git", "status", "--porcelain=v1", "--untracked-files=all"]
        return _cp(stdout="")

    assert check_clean_tree(run) == 0
    assert "check-clean-tree: clean" in capsys.readouterr().out


def test_check_clean_tree_fails_when_dirty(capsys) -> None:
    def run(argv: Sequence[str]) -> CompletedProcess[str]:
        return _cp(stdout=" M Makefile" + chr(10) + "?? scripts/new_guard.py" + chr(10))

    assert check_clean_tree(run) == 1
    out = capsys.readouterr().out
    assert "BLOCKED: working tree is dirty" in out
    assert "M Makefile" in out
    assert "?? scripts/new_guard.py" in out


def test_check_clean_tree_fails_when_git_status_fails(capsys) -> None:
    def run(argv: Sequence[str]) -> CompletedProcess[str]:
        return _cp(stderr="not a git repo", returncode=128)

    assert check_clean_tree(run) == 1
    assert "unable to verify clean tree" in capsys.readouterr().err


def test_check_clean_tree_source_is_not_bypassed() -> None:
    source = Path("scripts/check_clean_tree.py").read_text()
    assert "Temporarily bypassed" not in source
    assert "sys.exit(0)" not in source
    assert "git status" in source
