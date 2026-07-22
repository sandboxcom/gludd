from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from worktree_state_guard import current_state, format_claim_token, main  # noqa: E402


def _completed(
    argv: Sequence[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def test_current_state_is_path_qualified_and_counts_dirty_entries() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/repo" + newline)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "release-sync-beta1" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc1234" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            status = " M Makefile" + newline + "A  scripts/new.py" + newline + "?? notes.txt" + newline
            return _completed(args, status)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = current_state(run=run)

    assert state.path == "/repo"
    assert state.branch == "release-sync-beta1"
    assert state.head == "abc1234"
    assert state.dirty_count == 3
    assert state.staged_count == 1
    assert state.untracked_count == 1
    assert not state.is_clean


def test_claim_token_requires_clean_path_branch_and_head() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        responses = {
            ("git", "rev-parse", "--show-toplevel"): "/repo" + newline,
            ("git", "branch", "--show-current"): "agent-clean" + newline,
            ("git", "rev-parse", "--verify", "HEAD"): "dbe99221" + newline,
            ("git", "status", "--porcelain=v1", "--untracked-files=all"): "",
        }
        return _completed(args, responses[tuple(args)])

    token = format_claim_token(current_state(run=run), checked_at=1234567890)

    assert token == "WORKTREE-CLEAN path=/repo branch=agent-clean head=dbe99221 dirty=0 checked_at=1234567890"


def test_assert_clean_blocks_dirty_state(capsys) -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/repo" + newline)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "main" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "badc0de" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, " M pyproject.toml" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    rc = main(["--assert-clean"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "WORKTREE-DIRTY" in captured.out
    assert "path=/repo" in captured.out
    assert "dirty=1" in captured.out
