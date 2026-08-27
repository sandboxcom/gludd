"""Branch coverage for fail-closed Git release topology evidence."""

from __future__ import annotations

import builtins
import os
import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

from general_ludd.git_release import topology


def test_run_git_returns_empty_evidence_when_process_launch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Git executable must become absent evidence, not an exception."""

    def fail_run(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("git unavailable")

    monkeypatch.setattr(subprocess, "run", fail_run)

    assert topology._run_git("/repo", "status") == ""


def test_git_dir_resolves_linked_worktree_and_handles_unreadable_pointer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linked-worktree pointers resolve, while unreadable pointers fail closed."""
    repo = tmp_path / "repo"
    common = tmp_path / "common"
    repo.mkdir()
    common.mkdir()
    pointer = repo / ".git"
    pointer.write_text(f"gitdir: {common}\n", encoding="utf-8")

    assert topology._git_dir(str(repo)) == str(common)

    def deny_open(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("permission denied")

    monkeypatch.setattr(builtins, "open", deny_open)
    assert topology._git_dir(str(repo)) is None


def test_repository_evidence_time_falls_back_to_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid commit time plus missing HEAD metadata has a stable epoch fallback."""
    monkeypatch.setattr(topology, "_run_git", lambda *args: "not-a-time")

    def fail_stat(path: os.PathLike[str] | str) -> NoReturn:
        raise OSError(f"missing: {path}")

    monkeypatch.setattr(os, "stat", fail_stat)

    observed = topology._repository_evidence_time("/repo", "/git", "1" * 40)

    assert observed == "1970-01-01T00:00:00+00:00"


def test_detect_operations_reports_every_recoverable_marker(tmp_path: Path) -> None:
    """Every in-flight Git marker is represented with an explicit recovery ID."""
    (tmp_path / "rebase-merge").mkdir()
    for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "BISECT_LOG"):
        (tmp_path / marker).touch()

    operations = topology._detect_operations(str(tmp_path))

    assert {operation.kind for operation in operations} == {
        "rebase",
        "merge",
        "cherry-pick",
        "bisect",
    }
    assert all(
        operation.recovery_command_id is not None and operation.recovery_command_id.startswith("git.")
        for operation in operations
    )


def test_parse_porcelain_ignores_metadata_and_malformed_records() -> None:
    """Porcelain metadata and incomplete records cannot become dirty paths."""
    dirty = topology._parse_porcelain(
        [
            "",
            "# branch.oid " + "1" * 40,
            "malformed",
            "1 M. N... 100644 100644 100644 abc abc tracked.txt",
            "2 R. N... 100644 100644 100644 abc abc R100 renamed.txt",
            "? new.txt",
        ]
    )

    assert [item.path for item in dirty] == ["tracked.txt", "renamed.txt", "new.txt"]
    assert dirty[0].index_state == "modified"
    assert dirty[1].index_state == "renamed"
    assert dirty[2].untracked is True


def test_collect_policies_skips_comments_and_handles_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only active attributes are evidence and unreadable policy fails closed."""
    (tmp_path / ".gitattributes").write_text("\n# comment\n*.py text\n", encoding="utf-8")
    policies = topology._collect_policies(str(tmp_path))
    assert [policy.rule_id for policy in policies] == ["*.py"]

    def deny_open(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("permission denied")

    monkeypatch.setattr(builtins, "open", deny_open)
    assert topology._collect_policies(str(tmp_path)) == []


def test_parse_worktrees_flushes_separated_and_final_records() -> None:
    """Both blank-delimited and trailing worktree records are retained."""
    dirty = topology._parse_porcelain(["? scratch.txt"])
    worktrees = topology._parse_worktrees(
        [
            "worktree /repo",
            "HEAD " + "1" * 40,
            "branch refs/heads/main",
            "",
            "worktree /repo-linked",
            "HEAD " + "2" * 40,
            "detached",
        ],
        dirty,
    )

    assert [worktree.path for worktree in worktrees] == ["/repo", "/repo-linked"]
    assert worktrees[0].branch == "main"
    assert worktrees[1].branch is None
    assert all(worktree.dirty for worktree in worktrees)


def test_assess_repo_rejects_regular_file(tmp_path: Path) -> None:
    """A regular file is not a repository root."""
    candidate = tmp_path / "repo.txt"
    candidate.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="not a directory"):
        topology.assess_repo(str(candidate))


def test_collect_upstreams_handles_absent_valid_and_malformed_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upstream evidence is emitted only for complete numeric divergence data."""
    assert topology._collect_upstreams("/repo", None) == []

    monkeypatch.setattr(topology, "_run_git", lambda *args: "")
    assert topology._collect_upstreams("/repo", "main") == []

    def valid_git(repo: str, *args: str) -> str:
        del repo
        return "2 3" if args[0] == "rev-list" else "origin/main"

    monkeypatch.setattr(topology, "_run_git", valid_git)
    upstreams = topology._collect_upstreams("/repo", "main")
    assert len(upstreams) == 1
    assert upstreams[0].behind == 2
    assert upstreams[0].ahead == 3

    def malformed_git(repo: str, *args: str) -> str:
        del repo
        return "invalid" if args[0] == "rev-list" else "origin/main"

    monkeypatch.setattr(topology, "_run_git", malformed_git)
    assert topology._collect_upstreams("/repo", "main") == []
