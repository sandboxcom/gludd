from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from ci_remote_head_guard import collect_state, guard_state, main  # noqa: E402


def _completed(
    argv: Sequence[str],
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def test_collect_state_defaults_empty_ref_and_remote_to_current_branch() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "abc123" + chr(9) + "refs/heads/development" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("", "", run=run)

    assert state.branch == "development"
    assert state.remote == "sandboxcom"
    assert state.remote_ref == "refs/heads/development"
    assert state.remote_head == "abc123"
    assert guard_state(state) == []


def test_guard_blocks_stale_remote_head() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "release-sync" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "newhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/release-sync"]:
            return _completed(args, "oldhead" + chr(9) + "refs/heads/release-sync" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("release-sync", "sandboxcom", run=run)

    errors = guard_state(state)

    assert errors == ["remote sandboxcom/refs/heads/release-sync is oldhead, not local HEAD newhead"]


def test_guard_blocks_dirty_local_state_for_remote_ci() -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, " M Makefile" + newline + "?? scripts/new_guard.py" + newline)
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "abc123" + chr(9) + "refs/heads/development" + newline)
        raise AssertionError(f"unexpected argv: {args!r}")

    state = collect_state("development", "sandboxcom", run=run)

    errors = guard_state(state)

    assert "2 local dirty path(s) would make local tests differ from remote CI" in errors


def test_main_reports_blocked_state_for_missing_remote_branch(capsys) -> None:
    newline = chr(10)

    def run(argv: Sequence[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "feature-x" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "abc123" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/feature-x"]:
            return _completed(args, "")
        raise AssertionError(f"unexpected argv: {args!r}")

    rc = main(["--ref", "feature-x"], run=run)

    captured = capsys.readouterr()
    assert rc == 1
    assert "REMOTE-HEAD-BLOCKED" in captured.out
    assert "remote branch sandboxcom/refs/heads/feature-x does not exist" in captured.out


def test_guard_script_uses_system_python_compatible_optional_annotations() -> None:
    script = (ROOT / "scripts" / "ci_remote_head_guard.py").read_text()

    assert " | None" not in script
    assert "from typing import Optional" in script
