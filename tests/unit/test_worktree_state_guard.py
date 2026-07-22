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



def test_parse_worktree_paths_from_porcelain_inventory() -> None:
    newline = chr(10)
    from worktree_state_guard import parse_worktree_paths

    output = (
        "worktree /Users/shawnwilson/gludd" + newline
        + "HEAD abc1234" + newline
        + "branch refs/heads/master" + newline
        + newline
        + "worktree /tmp/gludd-worktrees/agent-clean" + newline
        + "HEAD def5678" + newline
        + "branch refs/heads/agent-clean" + newline
    )

    assert parse_worktree_paths(output) == ["/Users/shawnwilson/gludd", "/tmp/gludd-worktrees/agent-clean"]


def test_main_worktree_guard_fails_when_canonical_checkout_is_dirty(capsys) -> None:
    newline = chr(10)
    calls: list[tuple[tuple[str, ...], str | None]] = []

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        calls.append((tuple(args), cwd))
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(
                args,
                "worktree /Users/shawnwilson/gludd" + newline
                + "HEAD abc1234" + newline
                + "branch refs/heads/master" + newline
                + newline
                + "worktree /tmp/gludd-worktrees/agent-clean" + newline
                + "HEAD def5678" + newline
                + "branch refs/heads/agent-clean" + newline,
            )
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, (cwd or "/tmp/gludd-worktrees/agent-clean") + newline)
        if args == ["git", "branch", "--show-current"]:
            branch = "master" if cwd == "/Users/shawnwilson/gludd" else "agent-clean"
            return _completed(args, branch + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            head = "abc1234" if cwd == "/Users/shawnwilson/gludd" else "def5678"
            return _completed(args, head + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            status = ""
            if cwd == "/Users/shawnwilson/gludd":
                status = " M Makefile" + newline + "?? tests/unit/new_test.py" + newline
            return _completed(args, status)
        raise AssertionError(f"unexpected argv: {args!r} cwd={cwd!r}")

    rc = main(["--main-path", "/Users/shawnwilson/gludd", "--assert-main-clean"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "MAIN-WORKTREE-DIRTY" in captured.out
    assert "path=/Users/shawnwilson/gludd" in captured.out
    assert "dirty=2" in captured.out
    assert (("git", "status", "--porcelain=v1", "--untracked-files=all"), "/Users/shawnwilson/gludd") in calls


def test_main_worktree_guard_can_emit_clean_claim_token(capsys) -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "worktree", "list", "--porcelain"]:
            return _completed(args, "worktree /Users/shawnwilson/gludd" + newline)
        if args == ["git", "rev-parse", "--show-toplevel"]:
            return _completed(args, "/Users/shawnwilson/gludd" + newline)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "master" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc1234" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        raise AssertionError(f"unexpected argv: {args!r} cwd={cwd!r}")

    rc = main(["--main-path", "/Users/shawnwilson/gludd", "--assert-main-clean", "--main-claim-token"], run=run)

    captured = capsys.readouterr()
    assert rc == 0
    assert "MAIN-WORKTREE-CLEAN path=/Users/shawnwilson/gludd branch=master head=abc1234 dirty=0" in captured.out
