"""Hardening tests for general_ludd.worktree.core git worktree operations.

These prove that branch names and worktree paths supplied by callers are
validated before any `git worktree` subprocess runs:

- branch names with a leading '-' or shell/ref metacharacters are rejected,
- worktree paths are confined (realpath) to an allowed base directory and
  traversal ('..') / out-of-base paths are rejected,
- a normal add / list / remove builds the expected argv (list-form, with a
  '--' end-of-options separator before the path positional), asserted by
  mocking subprocess so no real git runs.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.worktree.core import (
    WorktreeMonitorConfig,
    WorktreeScanner,
    build_worktree_add_argv,
    build_worktree_list_argv,
    build_worktree_remove_argv,
    confine_worktree_path,
    validate_branch_name,
)


def _ok(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --------------------------------------------------------------------------- #
# branch-name validation                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad",
    [
        "-rf",                       # leading dash -> parsed as a git option
        "--upload-pack=touch /tmp/x",
        "-b",
        "feat;rm -rf /",             # shell metachar
        "feat$(whoami)",             # command substitution
        "feat`id`",                  # backtick
        "feat|tee",                  # pipe
        "feat&bg",                   # ampersand
        "feat\nrm",                  # newline
        "feat branch",               # whitespace
        "feat\x00null",              # NUL / control char
        "",                          # empty
        "   ",                       # whitespace only
        "feat..fix",                 # git refuses '..' in a ref
        "feat~1",                    # '~' is a ref operator
        "feat^",                     # '^' is a ref operator
        "feat:remote",               # ':' is a refspec separator
        "feat?",                     # glob char git forbids in a ref
        "/feat",                     # leading slash
        "feat.lock",                 # git forbids a ref ending in .lock
    ],
)
def test_validate_branch_name_rejects_injection(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_branch_name(bad)


@pytest.mark.parametrize(
    "good",
    [
        "feature/login",
        "fix-42",
        "agent/TODO-7/slug-20260101010101",
        "release_1.2.3",
        "main",
    ],
)
def test_validate_branch_name_accepts_normal(good: str) -> None:
    assert validate_branch_name(good) == good


# --------------------------------------------------------------------------- #
# path confinement                                                            #
# --------------------------------------------------------------------------- #


def test_confine_worktree_path_accepts_within_base(tmp_path) -> None:
    base = tmp_path / "worktrees"
    base.mkdir()
    target = base / "wt-1"
    resolved = confine_worktree_path(str(target), str(base))
    # Returns a realpath'd absolute path under the base.
    assert resolved.startswith(str(base.resolve()))
    assert resolved.endswith("wt-1")


def test_confine_worktree_path_rejects_traversal(tmp_path) -> None:
    base = tmp_path / "worktrees"
    base.mkdir()
    with pytest.raises(ValueError):
        confine_worktree_path(str(base / ".." / ".." / "etc"), str(base))


def test_confine_worktree_path_rejects_absolute_outside_base(tmp_path) -> None:
    base = tmp_path / "worktrees"
    base.mkdir()
    with pytest.raises(ValueError):
        confine_worktree_path("/etc/passwd", str(base))


def test_confine_worktree_path_rejects_leading_dash(tmp_path) -> None:
    base = tmp_path / "worktrees"
    base.mkdir()
    with pytest.raises(ValueError):
        confine_worktree_path("--force", str(base))


def test_confine_worktree_path_rejects_symlink_escape(tmp_path) -> None:
    base = tmp_path / "worktrees"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "escape"
    link.symlink_to(outside)
    # realpath must resolve the symlink and reject the out-of-base target.
    with pytest.raises(ValueError):
        confine_worktree_path(str(link), str(base))


# --------------------------------------------------------------------------- #
# argv builders                                                               #
# --------------------------------------------------------------------------- #


def test_build_worktree_add_argv_shape(tmp_path) -> None:
    base = tmp_path / "wts"
    base.mkdir()
    argv = build_worktree_add_argv(
        repo_path=str(tmp_path / "repo"),
        branch_name="feature/login",
        worktree_path=str(base / "wt-1"),
        allowed_base=str(base),
    )
    assert isinstance(argv, list)
    assert argv[0] == "git"
    # repo is selected via `-C <repo>` (argv element, never shell-interpolated)
    assert "-C" in argv
    assert argv[argv.index("-C") + 1] == str(tmp_path / "repo")
    # the `worktree add -b <branch>` subcommand
    assert "worktree" in argv and "add" in argv
    assert argv[argv.index("add") + 1] == "-b"
    assert argv[argv.index("-b") + 1] == "feature/login"
    # `--` must separate options from the path positional.
    assert "--" in argv
    sep = argv.index("--")
    # path positional comes after the `--`
    assert argv[sep + 1] == confine_worktree_path(str(base / "wt-1"), str(base))
    # the branch name must appear as its own argv element (no shell string)
    assert "feature/login" in argv


def test_build_worktree_add_argv_rejects_bad_branch(tmp_path) -> None:
    base = tmp_path / "wts"
    base.mkdir()
    with pytest.raises(ValueError):
        build_worktree_add_argv(
            repo_path=str(tmp_path / "repo"),
            branch_name="-rf",
            worktree_path=str(base / "wt"),
            allowed_base=str(base),
        )


def test_build_worktree_add_argv_rejects_escaping_path(tmp_path) -> None:
    base = tmp_path / "wts"
    base.mkdir()
    with pytest.raises(ValueError):
        build_worktree_add_argv(
            repo_path=str(tmp_path / "repo"),
            branch_name="feat",
            worktree_path="/etc/evil",
            allowed_base=str(base),
        )


def test_build_worktree_remove_argv_shape(tmp_path) -> None:
    base = tmp_path / "wts"
    base.mkdir()
    target = base / "wt-1"
    argv = build_worktree_remove_argv(
        worktree_path=str(target),
        allowed_base=str(base),
    )
    assert argv[:4] == ["git", "worktree", "remove", "--force"]
    assert "--" in argv
    sep = argv.index("--")
    assert argv[sep + 1] == confine_worktree_path(str(target), str(base))
    assert argv[-1] == argv[sep + 1]


def test_build_worktree_remove_argv_rejects_escape(tmp_path) -> None:
    base = tmp_path / "wts"
    base.mkdir()
    with pytest.raises(ValueError):
        build_worktree_remove_argv(
            worktree_path="../../etc",
            allowed_base=str(base),
        )


def test_build_worktree_list_argv_shape() -> None:
    # No repo selector: bare list form.
    assert build_worktree_list_argv() == ["git", "worktree", "list", "--porcelain"]
    # With a repo, selected via `-C <repo>` (argv element).
    argv = build_worktree_list_argv(repo_path="/repo")
    assert argv == ["git", "-C", "/repo", "worktree", "list", "--porcelain"]
    assert isinstance(argv, list)


# --------------------------------------------------------------------------- #
# reclaim path validation at the existing call site                          #
# --------------------------------------------------------------------------- #


@patch("subprocess.run")
def test_reclaim_validates_path_before_subprocess(mock_run: MagicMock, tmp_path) -> None:
    """An injection-y reclaim path must NOT reach subprocess (fail closed)."""
    scanner = WorktreeScanner(WorktreeMonitorConfig())
    # A leading-dash path would be parsed by git as an option; reclaim must
    # refuse it and never invoke subprocess.
    scanner._reclaim_worktree_dir("--force")
    mock_run.assert_not_called()


@patch("subprocess.run")
def test_reclaim_normal_path_builds_remove_argv(mock_run: MagicMock, tmp_path) -> None:
    mock_run.return_value = _ok()
    wt = tmp_path / "wt-1"
    wt.mkdir()
    scanner = WorktreeScanner(WorktreeMonitorConfig())
    scanner._reclaim_worktree_dir(str(wt))
    assert mock_run.called
    first_argv = mock_run.call_args_list[0].args[0]
    assert first_argv[:5] == ["git", "-C", str(wt), "worktree", "remove"]
    assert "--force" in first_argv
    assert "--" in first_argv
    # path positional is the realpath of the worktree, after the `--`
    sep = first_argv.index("--")
    assert first_argv[sep + 1] == str(wt.resolve())
