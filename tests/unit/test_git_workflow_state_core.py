from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

from general_ludd.git_automation.repo import GitAutomation


def _completed(argv: Sequence[str], stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), returncode, stdout, "")


def test_workflow_state_reports_clean_remote_ready_state() -> None:
    newline = chr(10)

    def fake_run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "development" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, "")
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/development"]:
            return _completed(args, "devhead" + chr(9) + "refs/heads/development" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", 0)
        raise AssertionError(f"unexpected argv: {args!r}")

    with patch("general_ludd.git_automation.repo.subprocess.run", side_effect=fake_run):
        state = GitAutomation(repo_path="/repo").workflow_state(
            assert_clean=True,
            assert_remote_head=True,
            assert_merge_ready=True,
        )

    assert state.success is True
    assert state.branch == "development"
    assert state.remote_head == "devhead"
    assert state.master_is_ancestor_of_development is True
    assert state.errors == []


def test_workflow_state_blocks_dirty_stale_and_cherry_pick_topology() -> None:
    newline = chr(10)

    def fake_run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        args = list(argv)
        if args == ["git", "branch", "--show-current"]:
            return _completed(args, "master" + newline)
        if args == ["git", "rev-parse", "--verify", "HEAD"]:
            return _completed(args, "newhead" + newline)
        if args == ["git", "status", "--porcelain=v1", "--untracked-files=all"]:
            return _completed(args, " M Makefile" + newline)
        if args == ["git", "ls-remote", "sandboxcom", "refs/heads/master"]:
            return _completed(args, "oldhead" + chr(9) + "refs/heads/master" + newline)
        if args == ["git", "rev-parse", "--verify", "master"]:
            return _completed(args, "masterhead" + newline)
        if args == ["git", "rev-parse", "--verify", "development"]:
            return _completed(args, "devhead" + newline)
        if args == ["git", "merge-base", "--is-ancestor", "masterhead", "devhead"]:
            return _completed(args, "", 1)
        raise AssertionError(f"unexpected argv: {args!r}")

    with patch("general_ludd.git_automation.repo.subprocess.run", side_effect=fake_run):
        state = GitAutomation(repo_path="/repo").workflow_state(
            assert_clean=True,
            assert_no_feature_on_master=True,
            assert_merge_ready=True,
            assert_remote_head=True,
            assert_gha_matches_local=True,
            gha_head_sha="oldhead",
        )

    assert state.success is False
    assert "1 dirty path(s) make local test evidence unreproducible in GHA" in state.errors
    merge_error = (
        "master has commits not contained in development; "
        "repair topology before release merge, do not cherry-pick"
    )
    assert merge_error in state.errors
    assert "remote sandboxcom/refs/heads/master is oldhead, not local HEAD newhead" in state.errors
    assert "latest GHA head oldhead does not match local HEAD newhead" in state.errors
